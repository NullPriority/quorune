from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.attachments import attach_objects
from quorune.compiler.continuous_templates import (
    controlled_characteristic_until_end_of_turn_effect,
    controlled_creature_fixed_modifier,
    controlled_creature_until_end_of_turn_effect,
    fixed_power_toughness_anthem_handler,
)
from quorune.compiler.fixed_public_characteristic_sets import (
    FIXED_PUBLIC_CHARACTERISTIC_SET_TEMPLATE_ID,
    fixed_public_characteristic_set_effect_template,
)
from quorune.compiler.fixed_resolution_characteristic_queries import (
    fixed_resolution_characteristic_query_is_closed,
)
from quorune.compiler.dependency_gate import DependencyGate
from quorune.characteristic_evaluation import (
    evaluate_card_characteristics,
    type_parts,
)
from quorune.continuous_effect_state import (
    expire_end_of_turn_continuous_effects,
)
from quorune.continuous_effects import (
    CharacteristicState,
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectError,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
    evaluate_continuous_effects,
)
from quorune.model import CardInstance, CombatState, GameState, StackItem
from quorune.object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    ContinuousEffectSourceContext,
    FixedQueryPowerToughnessAnthemHandler,
    SemanticSourceContext,
    SemanticNodeError,
    default_semantic_interpreter,
)
from quorune.semantic_runtime.intents import (
    SetCardDesignationIntent,
)
from quorune.semantics import SemanticProgram


def activated_characteristic_card(text: str) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000611200",
        name="Activated Characteristic Fixture",
        mana_cost="{2}",
        mana_value=2.0,
        type_line="Artifact Creature — Test",
        oracle_text=text,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def characteristic_card(
    text: str,
    *,
    type_line: str,
    name: str,
    oracle_suffix: int,
) -> CardRecord:
    creature = "Creature" in type_line
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{oracle_suffix:012d}",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if creature else None,
        toughness="2" if creature else None,
        loyalty="4" if "Planeswalker" in type_line else None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def locked_effect(
    *identities: ContinuousObjectIdentity,
) -> ContinuousEffect:
    return ContinuousEffect(
        effect_id="CE1",
        source_id="S1",
        layer=Layer.POWER_TOUGHNESS,
        sublayer="7c",
        timestamp=1,
        operations=(
            ContinuousOperation("modify_power_toughness", [2, 2]),
        ),
        origin=ContinuousEffectOrigin.RESOLUTION,
        duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
        applies=ObjectQuerySpec(zones=("battlefield",)),
        locked_objects=identities,
    )


class ContinuousEffectModelTests(unittest.TestCase):
    def test_type_parser_preserves_hyphenated_printed_subtypes(self):
        card_types, subtypes, supertypes = type_parts(
            "Artifact Creature — Assembly-Worker"
        )
        self.assertEqual({"artifact", "creature"}, card_types)
        self.assertEqual({"assembly-worker"}, subtypes)
        self.assertEqual(set(), supertypes)

    def test_type_parser_preserves_time_lord_as_one_creature_subtype(self):
        card_types, subtypes, supertypes = type_parts(
            "Legendary Creature — Time Lord Doctor"
        )
        self.assertEqual({"creature"}, card_types)
        self.assertEqual({"time lord", "doctor"}, subtypes)
        self.assertEqual({"legendary"}, supertypes)

    def test_power_toughness_layer_preserves_printed_type_line(self):
        card = CardInstance(
            object_id="object",
            ref="A01",
            oracle_id="oracle",
            printed_name="Worker",
            owner="A",
            controller="A",
            zone="battlefield",
        )
        result = evaluate_card_characteristics(
            card,
            {
                "name": "Worker",
                "mana_cost": "{2}",
                "mana_value": 2,
                "type_line": "Artifact Creature — Assembly-Worker",
                "oracle_text": "",
                "power": "1",
                "toughness": "1",
                "keywords": [],
                "colors": [],
            },
            runtime_effects=(
                locked_effect(
                    ContinuousObjectIdentity(
                        card.object_id, card.logical_object_id
                    )
                ),
            ),
        )
        self.assertEqual(
            "Artifact Creature — Assembly-Worker", result["type_line"]
        )
        self.assertEqual("3", result["power"])

    def test_operation_and_effect_input_trees_are_deeply_immutable(self):
        supplied = {"name": "Before", "abilities": ["Flying"]}
        operation = ContinuousOperation("copy_values", supplied)
        supplied["name"] = "After"
        supplied["abilities"].append("Haste")

        effect = ContinuousEffect(
            effect_id="copy",
            source_id="source",
            layer=Layer.COPY,
            sublayer="1a",
            timestamp=1,
            operations=(operation,),
        )
        self.assertEqual("Before", operation.value["name"])
        self.assertEqual(("Flying",), operation.value["abilities"])
        before = effect.fingerprint
        serialized = effect.to_dict()
        serialized["operations"][0]["value"]["abilities"].append(
            "Trample"
        )
        self.assertEqual(before, effect.fingerprint)

    def test_copy_values_preserve_duplicate_ability_instances_only(self):
        operation = ContinuousOperation(
            "copy_values",
            {"abilities": ["Toxic 1", "Toxic 1"]},
        )
        self.assertEqual(
            ("Toxic 1", "Toxic 1"), operation.value["abilities"]
        )
        with self.assertRaisesRegex(ContinuousEffectError, "unique"):
            ContinuousOperation(
                "copy_values",
                {"colors": ["G", "g"]},
            )

    def test_canonical_round_trip_and_construction_order_share_fingerprint(self):
        first = ContinuousOperation(
            "copy_values", {"colors": ["G"], "name": "Copy"}
        )
        second = ContinuousOperation(
            "copy_values", {"name": "Copy", "colors": ["G"]}
        )
        left = ContinuousEffect(
            effect_id="copy",
            source_id="source",
            layer=Layer.COPY,
            sublayer="1a",
            timestamp=4,
            operations=(first,),
        )
        right = ContinuousEffect(
            effect_id="copy",
            source_id="source",
            layer=Layer.COPY,
            sublayer="1a",
            timestamp=4,
            operations=(second,),
        )
        self.assertEqual(left.fingerprint, right.fingerprint)
        self.assertEqual(
            left.to_dict(), ContinuousEffect.from_dict(left.to_dict()).to_dict()
        )

    def test_malformed_models_fail_closed(self):
        with self.assertRaisesRegex(
            ContinuousEffectError, "ObjectQuerySpec"
        ):
            ContinuousEffect(
                effect_id="bad",
                source_id="source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=0,
                operations=(ContinuousOperation("add_ability", "Flying"),),
                applies={},
            )
        with self.assertRaisesRegex(ContinuousEffectError, "locked object"):
            ContinuousEffect(
                effect_id="bad",
                source_id="source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=0,
                operations=(ContinuousOperation("add_ability", "Flying"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
            )
        payload = locked_effect(
            ContinuousObjectIdentity("object", "object@0")
        ).to_dict()
        payload["unknown"] = True
        with self.assertRaisesRegex(ContinuousEffectError, "fields"):
            ContinuousEffect.from_dict(payload)
        scalar_payload = locked_effect(
            ContinuousObjectIdentity("object", "object@0")
        ).to_dict()
        scalar_payload["timestamp"] = "1"
        with self.assertRaisesRegex(ContinuousEffectError, "scalar fields"):
            ContinuousEffect.from_dict(scalar_payload)
        operation_payload = ContinuousOperation(
            "add_ability", "Flying"
        ).to_dict()
        operation_payload["op"] = 6
        with self.assertRaisesRegex(ContinuousEffectError, "names"):
            ContinuousOperation.from_dict(operation_payload)
        with self.assertRaisesRegex(ContinuousEffectError, "integer pair"):
            ContinuousOperation("modify_power_toughness", [1, True])
        with self.assertRaisesRegex(ContinuousEffectError, "integer layer"):
            ContinuousEffect(
                effect_id="boolean-layer",
                source_id="source",
                layer=True,
                sublayer="1a",
                timestamp=0,
                operations=(
                    ContinuousOperation("copy_values", {"name": "Copy"}),
                ),
            )
        with self.assertRaisesRegex(ContinuousEffectError, "not in layer"):
            ContinuousEffect(
                effect_id="unknown-sublayer",
                source_id="source",
                layer=Layer.ABILITY,
                sublayer="6x",
                timestamp=0,
                operations=(ContinuousOperation("add_ability", "Flying"),),
            )
        with self.assertRaisesRegex(ContinuousEffectError, "unknown fields"):
            ContinuousOperation("face_down", {"forged": True})
        with self.assertRaisesRegex(ContinuousEffectError, "does not accept"):
            ContinuousOperation("add_ability", "Flying", field="abilities")
        with self.assertRaisesRegex(ContinuousEffectError, "represented layer"):
            ContinuousEffect(
                effect_id="wrong-layer",
                source_id="source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=0,
                operations=(
                    ContinuousOperation("modify_power_toughness", [1, 1]),
                ),
            )

    def test_resolution_set_is_locked_to_logical_objects(self):
        effect = locked_effect(
            ContinuousObjectIdentity("object", "object@0")
        )

        def evaluate(object_id: str, incarnation: str, subtype: str):
            return evaluate_continuous_effects(
                CharacteristicState(
                    name="Creature",
                    controller="A",
                    card_types={"Creature"},
                    subtypes={subtype},
                    power=1,
                    toughness=1,
                ),
                (effect,),
                context={
                    "object_id": object_id,
                    "logical_object_id": incarnation,
                    "ref": "A01",
                    "owner": "A",
                    "zone": "battlefield",
                },
            )

        self.assertEqual(3, evaluate("object", "object@0", "Elf").characteristics["power"])
        self.assertEqual(3, evaluate("object", "object@0", "Dragon").characteristics["power"])
        self.assertEqual(1, evaluate("new", "new@0", "Elf").characteristics["power"])
        self.assertEqual(1, evaluate("object", "object@1", "Elf").characteristics["power"])

    def test_resolution_and_static_ability_changes_share_layer_six_ordering(self):
        identity = ContinuousObjectIdentity("object", "object@0")
        grant = ContinuousEffect(
            effect_id="grant",
            source_id="resolved-source",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=1,
            operations=(ContinuousOperation("add_ability", "Flying"),),
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            applies=ObjectQuerySpec(zones=("battlefield",)),
            locked_objects=(identity,),
        )
        removal = ContinuousEffect(
            effect_id="removal",
            source_id="static-source",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=2,
            operations=(ContinuousOperation("remove_ability", "Flying"),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=ObjectQuerySpec(
                zones=("battlefield",),
                controller="A",
                types_all=("creature",),
            ),
        )
        context = {
            "object_id": "object",
            "logical_object_id": "object@0",
            "ref": "A01",
            "owner": "A",
            "zone": "battlefield",
        }
        state = CharacteristicState(
            name="Creature",
            controller="A",
            card_types={"Creature"},
            power=1,
            toughness=1,
        )
        removed = evaluate_continuous_effects(
            state,
            (grant, removal),
            context=context,
        )
        self.assertNotIn("Flying", removed.characteristics["abilities"])
        added_last = evaluate_continuous_effects(
            state,
            (removal, ContinuousEffect.from_dict({**grant.to_dict(), "timestamp": 3})),
            context=context,
        )
        self.assertIn("Flying", added_last.characteristics["abilities"])

    def test_duplicate_continuous_effect_ids_fail_closed(self):
        effect = locked_effect(
            ContinuousObjectIdentity("object", "object@0")
        )
        with self.assertRaisesRegex(ContinuousEffectError, "IDs must be unique"):
            evaluate_continuous_effects(
                CharacteristicState(
                    name="Creature",
                    controller="A",
                    card_types={"Creature"},
                    power=1,
                    toughness=1,
                ),
                (effect, effect),
                context={
                    "object_id": "object",
                    "logical_object_id": "object@0",
                    "ref": "A01",
                    "owner": "A",
                    "zone": "battlefield",
                },
            )

    def test_static_set_recomputes_and_source_presence_is_required(self):
        descriptor = fixed_power_toughness_anthem_handler(
            "Other Dragon creatures you control get +3/+3."
        )
        self.assertIsNotNone(descriptor)
        handler = FixedQueryPowerToughnessAnthemHandler()
        effect = handler.lower(
            descriptor[1],
            ContinuousEffectSourceContext(
                source_object_id="source",
                source_ref="A01",
                source_controller="A",
                source_timestamp=2,
                component_id="anthem",
            ),
        )[0]
        dragon = evaluate_continuous_effects(
            CharacteristicState(
                name="Dragon",
                controller="A",
                card_types={"Creature"},
                subtypes={"Dragon"},
                power=2,
                toughness=2,
            ),
            (effect,),
            context={"ref": "A02", "owner": "A"},
        )
        goblin = evaluate_continuous_effects(
            CharacteristicState(
                name="Goblin",
                controller="A",
                card_types={"Creature"},
                subtypes={"Goblin"},
                power=2,
                toughness=2,
            ),
            (effect,),
            context={"ref": "A02", "owner": "A"},
        )
        source = evaluate_continuous_effects(
            CharacteristicState(
                name="Source",
                controller="A",
                card_types={"Creature"},
                subtypes={"Dragon"},
                power=2,
                toughness=2,
            ),
            (effect,),
            context={"ref": "A01", "owner": "A"},
        )
        self.assertEqual(5, dragon.characteristics["power"])
        self.assertEqual(2, goblin.characteristics["power"])
        self.assertEqual(2, source.characteristics["power"])
        absent = copy.deepcopy(effect.to_dict())
        absent["source_present"] = False
        self.assertFalse(
            evaluate_continuous_effects(
                CharacteristicState(
                    name="Dragon",
                    controller="A",
                    card_types={"Creature"},
                    subtypes={"Dragon"},
                    power=2,
                    toughness=2,
                ),
                (ContinuousEffect.from_dict(absent),),
                context={"ref": "A02", "owner": "A"},
            ).applied_effects
        )

    def test_static_anthem_is_controller_scoped_in_four_player_evaluation(self):
        descriptor = fixed_power_toughness_anthem_handler(
            "Creatures you control get +1/+1."
        )
        effect = FixedQueryPowerToughnessAnthemHandler().lower(
            descriptor[1],
            ContinuousEffectSourceContext(
                source_object_id="source",
                source_ref="A01",
                source_controller="A",
                source_timestamp=2,
                component_id="multiplayer-anthem",
            ),
        )[0]
        powers = {}
        for seat in "ABCD":
            powers[seat] = evaluate_continuous_effects(
                CharacteristicState(
                    name=f"{seat} creature",
                    controller=seat,
                    card_types={"Creature"},
                    power=1,
                    toughness=1,
                ),
                (effect,),
                context={"ref": f"{seat}02", "owner": seat},
            ).characteristics["power"]
        self.assertEqual({"A": 2, "B": 1, "C": 1, "D": 1}, powers)

    def test_compiler_rejects_conditional_or_stateful_anthem_lookalikes(self):
        self.assertIsNone(
            controlled_creature_fixed_modifier(
                "Attacking creatures you control get +1/+0.",
                until_end_of_turn=False,
            )
        )
        self.assertIsNone(
            fixed_power_toughness_anthem_handler(
                "As long as you control ten lands, creatures you control get +2/+2."
            )
        )
        self.assertIsNotNone(
            controlled_creature_until_end_of_turn_effect(
                "Creatures you control get +1/+1 until end of turn."
            )
        )
        other = controlled_creature_until_end_of_turn_effect(
            "Other creatures you control get +1/+1 until end of turn."
        )
        self.assertEqual("$source", other[1][0]["predicate"]["exclude_ref"])

    def test_fixed_controlled_characteristics_compile_across_contexts(self):
        registry = load_default_capability_registry()
        cases = (
            (
                "spell",
                "Creatures you control gain flying until end of turn.",
                "Instant",
            ),
            (
                "triggered",
                (
                    "When this creature enters, other creatures you control "
                    "gain vigilance until end of turn."
                ),
                "Creature — Test",
            ),
            (
                "activated",
                (
                    "{2}: Artifact creatures you control gain flying until "
                    "end of turn."
                ),
                "Artifact Creature — Test",
            ),
            (
                "loyalty",
                (
                    "+1: Creatures you control gain vigilance until end of "
                    "turn."
                ),
                "Legendary Planeswalker — Test",
            ),
            (
                "modal",
                (
                    "Choose one —\n"
                    "• Rally — Creature tokens you control gain trample until "
                    "end of turn.\n"
                    "• Recover — You gain 3 life."
                ),
                "Sorcery",
            ),
        )
        for index, (context, text, type_line) in enumerate(cases):
            with self.subTest(context=context):
                ir = compile_oracle_card(
                    characteristic_card(
                        text,
                        type_line=type_line,
                        name=f"Characteristic {context}",
                        oracle_suffix=611_210 + index,
                    ),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual((), ir.material_residuals)
                self.assertIn(
                    "continuous.resolution.fixed_characteristics_until_end_of_turn",
                    {
                        capability
                        for node in ir.faces[0].nodes
                        for capability in node.capability_dependencies
                    },
                )

    def test_fixed_controlled_characteristic_query_grammar_is_closed(self):
        supported = (
            "Creatures you control gain flying until end of turn.",
            "Until end of turn, other creatures you control gain haste and trample.",
            "Creature tokens you control gain vigilance until end of turn.",
            "Nontoken creatures you control gain lifelink until end of turn.",
            "Red creatures you control gain first strike until end of turn.",
            "Colorless creatures you control gain menace until end of turn.",
            "Legendary creatures you control gain indestructible until end of turn.",
            "Goblin creatures you control get +1/+1 and gain vigilance until end of turn.",
            "Artifacts you control gain indestructible until end of turn.",
            "Lands you control gain hexproof until end of turn.",
            "Until end of turn, permanents you control gain shroud.",
            "Creatures you control with a +1/+1 counter on them gain "
            "trample until end of turn.",
        )
        for text in supported:
            with self.subTest(text=text):
                lowered = controlled_characteristic_until_end_of_turn_effect(text)
                self.assertIsNotNone(lowered)
                assert lowered is not None
                self.assertEqual(
                    "modify-controlled-fixed-characteristics-eot-v2",
                    lowered[0],
                )

        unsupported = (
            "Target creature you control gains flying until end of turn.",
            "Creatures you control get +X/+X until end of turn.",
            "Creatures you control gain protection from red until end of turn.",
            "Creatures you control gain \"{T}: Add {G}.\" until end of turn.",
            "Creatures you control become artifacts until end of turn.",
            "Creatures you control gain flying until your next turn.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(
                    controlled_characteristic_until_end_of_turn_effect(text)
                )

    def test_fixed_public_creature_sets_compile_across_shared_contexts(self):
        registry = load_default_capability_registry()
        cases = (
            (
                "spell-all",
                "All creatures get -2/-2 until end of turn.",
                "Sorcery",
                None,
                None,
                None,
            ),
            (
                "spell-opponents",
                "Creatures your opponents control get -1/-1 until end of turn.",
                "Instant",
                None,
                ["$controller"],
                None,
            ),
            (
                "spell-target-player",
                "Creatures target player controls get +2/+0 until end of turn.",
                "Sorcery",
                "$target.0",
                None,
                None,
            ),
            (
                "activated-attacking",
                "{2}: Attacking creatures gain trample until end of turn.",
                "Artifact",
                None,
                None,
                "attacking",
            ),
            (
                "loyalty-blocking",
                "+1: Blocking creatures get +0/+3 until end of turn.",
                "Legendary Planeswalker — Test",
                None,
                None,
                "blocking",
            ),
            (
                "triggered-other-attacking",
                "Whenever this creature attacks, other attacking creatures "
                "get +1/+0 until end of turn.",
                "Creature — Soldier",
                None,
                None,
                "attacking",
            ),
            (
                "spell-other-blocking",
                "Other blocking creatures get +1/+1 until end of turn.",
                "Instant",
                None,
                None,
                "blocking",
            ),
            (
                "modal",
                "Choose one —\n"
                "• Advance — Attacking creatures get +1/+0 until end of turn.\n"
                "• Recover — You gain 3 life.",
                "Sorcery",
                None,
                None,
                "attacking",
            ),
        )
        for index, (
            context,
            text,
            type_line,
            controller,
            excluded_controllers,
            state_field,
        ) in enumerate(cases):
            with self.subTest(context=context):
                ir = compile_oracle_card(
                    characteristic_card(
                        text,
                        type_line=type_line,
                        name=f"Public set {context}",
                        oracle_suffix=611_300 + index,
                    ),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                if context == "modal":
                    self.assertIn(
                        "continuous.resolution.fixed_characteristics_until_end_of_turn",
                        ir.faces[0].nodes[0].capability_dependencies,
                    )
                    continue
                nodes = [
                    node
                    for node in ir.faces[0].nodes
                    if any(
                        effect.get("op")
                        == "modify_all_matching_permanents_until_end_of_turn"
                        for effect in node.effects
                    )
                ]
                self.assertEqual(1, len(nodes))
                if context not in {"triggered-other-attacking", "modal"}:
                    self.assertEqual(
                        FIXED_PUBLIC_CHARACTERISTIC_SET_TEMPLATE_ID,
                        nodes[0].template_id,
                    )
                effect = next(
                    effect
                    for effect in nodes[0].effects
                    if effect.get("op")
                    == "modify_all_matching_permanents_until_end_of_turn"
                )
                predicate = effect["predicate"]
                self.assertTrue(
                    fixed_resolution_characteristic_query_is_closed(
                        ObjectQuerySpec.from_dict(predicate),
                        target_schema=nodes[0].target_schema,
                    )
                )
                self.assertEqual(controller, predicate["controller"])
                self.assertEqual(
                    excluded_controllers or [],
                    predicate.get("excluded_controllers", []),
                )
                state = predicate.get("state_predicate")
                if state_field is not None:
                    self.assertTrue(state[state_field])
                if context in {
                    "spell-other-blocking",
                    "triggered-other-attacking",
                }:
                    self.assertEqual("$source", predicate["exclude_ref"])
                if context == "spell-target-player":
                    self.assertEqual(
                        {
                            "zones": ["player"],
                            "categories": ["player"],
                            "player_relation": "any",
                            "count": 1,
                        },
                        nodes[0].target_schema,
                    )
                else:
                    self.assertIsNone(nodes[0].target_schema)

    def test_fixed_public_creature_set_near_misses_remain_material(self):
        registry = load_default_capability_registry()
        cases = (
            "Tapped creatures get -1/-1 until end of turn.",
            "Attacking creatures with flying get +1/+1 until end of turn.",
            "Creatures target opponent controls get -1/-1 until end of turn.",
            "Creatures your opponents control get +X/+X until end of turn.",
            "All creatures become artifacts until end of turn.",
            "All creatures get -1/-1 until your next turn.",
            "If you attacked, attacking creatures get +1/+0 until end of turn.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    characteristic_card(
                        text,
                        type_line="Instant",
                        name="Public set near miss",
                        oracle_suffix=611_320 + index,
                    ),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_public_creature_set_compiler_mutant_is_killed(self):
        record = characteristic_card(
            "All creatures get -1/-1 until end of turn.",
            type_line="Sorcery",
            name="Public set mutation",
            oracle_suffix=611_340,
        )
        registry = load_default_capability_registry()
        self.assertEqual(
            "exact",
            compile_oracle_card(
                record,
                capability_registry=registry,
                capability_profile="commander_review",
            ).status,
        )
        with patch(
            "quorune.oracle_ir.fixed_public_characteristic_set_effect_template",
            return_value=None,
        ):
            self.assertNotEqual(
                "exact",
                compile_oracle_card(
                    record,
                    capability_registry=registry,
                    capability_profile="commander_review",
                ).status,
            )

    def test_fixed_resolution_characteristic_shape_excludes_dynamic_queries(self):
        template = controlled_creature_until_end_of_turn_effect(
            "Other creatures you control get +1/+1 until end of turn."
        )
        self.assertIsNotNone(template)
        assert template is not None
        _template_id, effects, mechanics = template
        capability = (
            "continuous.resolution.fixed_characteristics_until_end_of_turn"
        )
        self.assertIn(
            capability,
            capability_dependencies_for_node(
                effects=effects,
                target_schema=None,
                mechanic_ids=mechanics,
            ),
        )
        effect = effects[0]
        for mutated in (
            {**effect, "power": {"kind": "dynamic"}},
            {
                **effect,
                "predicate": {
                    **effect["predicate"],
                    "state_predicate": {"kind": "attacking"},
                },
            },
            {
                **effect,
                "predicate": {
                    **effect["predicate"],
                    "controller": "$target.0",
                },
            },
        ):
            with self.subTest(effect=mutated):
                self.assertNotIn(
                    capability,
                    capability_dependencies_for_node(
                        effects=(mutated,),
                        target_schema=None,
                        mechanic_ids=mechanics,
                    ),
                )

    def test_fixed_resolution_keyword_shape_requires_exact_consumers(self):
        lowered = controlled_characteristic_until_end_of_turn_effect(
            "Other Goblin creatures you control get +1/+1 and gain flying "
            "until end of turn."
        )
        self.assertIsNotNone(lowered)
        assert lowered is not None
        _template_id, effects, mechanics = lowered
        capability = (
            "continuous.resolution.fixed_characteristics_until_end_of_turn"
        )
        self.assertIn(
            capability,
            capability_dependencies_for_node(
                effects=effects,
                target_schema=None,
                mechanic_ids=mechanics,
            ),
        )
        effect = effects[0]
        mutations = (
            ({**effect, "keywords": ["Flying", "Flying"]}, mechanics),
            ({**effect, "keywords": ["Protection"]}, mechanics),
            (
                effect,
                tuple(
                    mechanic
                    for mechanic in mechanics
                    if mechanic != "flying"
                ),
            ),
            (
                {
                    **effect,
                    "predicate": {
                        **effect["predicate"],
                        "controller": "$target.0",
                    },
                },
                mechanics,
            ),
        )
        for mutated_effect, mutated_mechanics in mutations:
            with self.subTest(effect=mutated_effect):
                self.assertNotIn(
                    capability,
                    capability_dependencies_for_node(
                        effects=(mutated_effect,),
                        target_schema=None,
                        mechanic_ids=mutated_mechanics,
                    ),
                )

    def test_activated_fixed_characteristic_effects_are_capability_closed(self):
        registry = load_default_capability_registry()
        cases = (
            (
                "{B}: This creature gets +1/+1 until end of turn.",
                "modify-self-creature-stats-eot-v1",
                "modify_stats_until_end_of_turn",
            ),
            (
                "{5}: Creatures you control get +1/+1 until end of turn.",
                "modify-controlled-creatures-fixed-stats-eot-v1",
                "modify_all_matching_permanents_until_end_of_turn",
            ),
            (
                "{4}: Other creatures you control get +1/+0 until end of turn.",
                "modify-controlled-creatures-fixed-stats-eot-v1",
                "modify_all_matching_permanents_until_end_of_turn",
            ),
        )
        for text, template_id, operation in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    activated_characteristic_card(text),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(operation, node.effects[0]["op"])
                self.assertIn(
                    "continuous.resolution.fixed_characteristics_until_end_of_turn",
                    node.capability_dependencies,
                )

        for keyword in (
            "deathtouch",
            "double strike",
            "first strike",
            "flying",
            "haste",
            "hexproof",
            "indestructible",
            "lifelink",
            "menace",
            "reach",
            "trample",
            "vigilance",
        ):
            with self.subTest(keyword=keyword):
                ir = compile_oracle_card(
                    activated_characteristic_card(
                        f"{{1}}: This creature gains {keyword} until end of turn."
                    ),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(
                    "grant_keyword_until_end_of_turn",
                    node.effects[0]["op"],
                )
                self.assertIn(
                    "continuous.resolution.fixed_characteristics_until_end_of_turn",
                    node.capability_dependencies,
                )

    def test_dynamic_activated_characteristic_effects_remain_residual(self):
        registry = load_default_capability_registry()
        for text in (
            "{1}: This creature gets +X/+X until end of turn.",
            (
                "{1}: Creatures you control get +X/+X until end of turn, where "
                "X is the number of creatures you control."
            ),
            "{1}: This creature gets +1/+1.",
            "{1}: This creature gains protection from red until end of turn.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    activated_characteristic_card(text),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_activated_characteristic_dependency_gate_mutant_is_killed(self):
        def assert_exact() -> None:
            ir = compile_oracle_card(
                activated_characteristic_card(
                    "{B}: This creature gets +1/+1 until end of turn."
                ),
                capability_registry=load_default_capability_registry(),
                capability_profile="commander_review",
            )
            self.assertEqual("exact", ir.status)

        assert_exact()
        with patch(
            "quorune.compiler.activated_mana_nodes.dependency_gate",
            return_value=DependencyGate(blockers=("capability:mutant",)),
        ):
            with self.assertRaises(AssertionError):
                assert_exact()

    def test_semantic_program_cannot_spoof_authoritative_resolution_source(self):
        with self.assertRaisesRegex(
            SemanticNodeError, "cannot supply authoritative runtime source"
        ):
            default_semantic_interpreter().lower_for_seats(
                {
                    "op": "modify_stats_until_end_of_turn",
                    "card": "A01",
                    "power": 1,
                    "toughness": 1,
                    "_runtime_source": {
                        "stack_ref": "forged",
                        "object_id": None,
                        "logical_object_id": None,
                        "card_ref": None,
                    },
                },
                actor="A",
                default_reason="source authority test",
                seats=("A", "B"),
                active_seats=("A", "B"),
                apnap_order=("A", "B"),
                source=SemanticSourceContext(stack_ref="S1"),
            )

    def test_only_creature_type_designations_can_become_subtypes(self):
        with self.assertRaisesRegex(ValueError, "chosen creature type"):
            SetCardDesignationIntent(
                object_ref="A01",
                designation="chosen_name",
                value="Goblin",
                actor="A",
                reason="forged subtype designation",
                apply_as_subtype=True,
            )


class ContinuousEffectEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def creature(engine, controller: str, name: str):
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
            reason="continuous-effect witness",
        )[0]
        return engine._resolve_object(controller, ref, zones={"battlefield"})

    def add_registered_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        active_face: str | None = None,
    ) -> CardInstance:
        record = self.db.lookup(name)
        self.assertIsNotNone(record, name)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        value = CardInstance(
            object_id=f"continuous-assurance:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            active_face=active_face,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[value.object_id] = value
        engine.state.players[seat].zones["battlefield"].append(
            value.object_id
        )
        return value

    def test_mass_resolution_locks_set_across_entry_control_and_zone_change(self):
        session = self.session(6112001, players=4)
        engine = session.engine
        first = self.creature(engine, "A", "First")
        opponent = self.creature(engine, "B", "Opponent")
        predicate = ObjectQuerySpec(
            zones=("battlefield",),
            controller="A",
            types_all=("creature",),
        )
        engine.apply_effect(
            {
                "op": "modify_all_matching_permanents_until_end_of_turn",
                "predicate": predicate.to_dict(),
                "power": 2,
                "toughness": 2,
            },
            actor="A",
        )
        later = self.creature(engine, "A", "Later")
        self.assertEqual(3, engine._numeric_stat(first.object_id, "power"))
        self.assertEqual(1, engine._numeric_stat(later.object_id, "power"))
        self.assertEqual(1, engine._numeric_stat(opponent.object_id, "power"))

        engine.change_control(first.object_id, "B", reason="locked-set test")
        self.assertEqual(3, engine._numeric_stat(first.object_id, "power"))
        engine.move_card(first.object_id, "graveyard", reason="identity test")
        engine.move_card(first.object_id, "battlefield", controller="B", reason="identity test")
        self.assertEqual(1, engine._numeric_stat(first.object_id, "power"))

    def test_fixed_controlled_characteristics_lock_effective_set_and_cleanup(self):
        session = self.session(6112010, players=4)
        engine = session.engine
        source = self.creature(engine, "A", "Source")
        first = self.creature(engine, "A", "First")
        second = self.creature(engine, "A", "Second")
        opponent = self.creature(engine, "B", "Opponent")
        artifact_ref = engine.create_token(
            "A",
            name="Animated artifact",
            characteristics={
                "type_line": "Token Artifact — Device",
                "power": "1",
                "toughness": "1",
            },
            reason="effective type boundary witness",
        )[0]
        artifact = engine._resolve_object(
            "A", artifact_ref, zones={"battlefield"}
        )
        engine.apply_effect(
            {
                "op": "add_types_until_end_of_turn",
                "card": artifact.ref,
                "types": ["Creature"],
            },
            actor="A",
        )
        engine.apply_effect(
            {
                "op": "modify_all_matching_permanents_until_end_of_turn",
                "predicate": ObjectQuerySpec(
                    zones=("battlefield",),
                    controller="A",
                    types_all=("creature",),
                    exclude_ref=source.ref,
                ).to_dict(),
                "power": 1,
                "toughness": 2,
                "keywords": ["Haste", "Trample"],
            },
            actor="A",
        )
        engine.apply_effect(
            {
                "op": "modify_all_matching_permanents_until_end_of_turn",
                "predicate": ObjectQuerySpec(
                    zones=("battlefield",),
                    controller="A",
                    types_all=("artifact", "creature"),
                ).to_dict(),
                "power": 0,
                "toughness": 0,
                "keywords": ["Flying"],
            },
            actor="A",
        )
        later = self.creature(engine, "A", "Later")

        first_data = engine._effective_card_data(first)
        artifact_data = engine._effective_card_data(artifact)
        self.assertEqual("2", first_data["power"])
        self.assertEqual("3", first_data["toughness"])
        self.assertGreaterEqual(
            set(first_data["keywords"]), {"Haste", "Trample"}
        )
        self.assertIn("Flying", artifact_data["keywords"])
        self.assertNotIn("Haste", engine._effective_card_data(source)["keywords"])
        self.assertNotIn("Haste", engine._effective_card_data(later)["keywords"])
        self.assertNotIn(
            "Haste", engine._effective_card_data(opponent)["keywords"]
        )

        engine.change_control(first.object_id, "B", reason="locked-set test")
        self.assertIn("Haste", engine._effective_card_data(first)["keywords"])
        engine.move_card(source.object_id, "graveyard", reason="source departure")
        self.assertIn("Haste", engine._effective_card_data(second)["keywords"])
        engine.move_card(first.object_id, "graveyard", reason="identity test")
        engine.move_card(
            first.object_id,
            "battlefield",
            controller="B",
            reason="identity test",
        )
        self.assertNotIn("Haste", engine._effective_card_data(first)["keywords"])

        projected = StateProjector(self.db, engine.state)._snapshot("pilot:C")
        rendered = json.dumps(projected, sort_keys=True)
        self.assertNotIn("continuous_effects", rendered)
        self.assertNotIn(second.object_id, rendered)
        self.assertGreater(expire_end_of_turn_continuous_effects(engine.state), 0)
        second_data = engine._effective_card_data(second)
        self.assertEqual("1", second_data["power"])
        self.assertEqual("1", second_data["toughness"])
        self.assertNotIn("Haste", second_data["keywords"])
        self.assertNotIn(
            "Flying", engine._effective_card_data(artifact)["keywords"]
        )

    def test_fixed_public_characteristic_sets_lock_multiplayer_membership(self):
        session = self.session(6112020, players=4)
        engine = session.engine
        source = self.creature(engine, "A", "Source")
        ally = self.creature(engine, "A", "Ally")
        opponent_b = self.creature(engine, "B", "Opponent B")
        opponent_c = self.creature(engine, "C", "Opponent C")
        opponent_d = self.creature(engine, "D", "Opponent D")
        engine.state.combat = CombatState(
            attackers={source.object_id: "B", ally.object_id: "B"},
            blockers={source.object_id: [opponent_b.object_id]},
        )
        source.attacking = "B"
        ally.attacking = "B"
        opponent_b.blocking = source.object_id
        effects = (
            (
                ObjectQuerySpec(
                    zones=("battlefield",),
                    types_all=("creature",),
                ),
                "Flying",
            ),
            (
                ObjectQuerySpec(
                    zones=("battlefield",),
                    excluded_controllers=("A",),
                    types_all=("creature",),
                ),
                "Menace",
            ),
            (
                ObjectQuerySpec(
                    zones=("battlefield",),
                    types_all=("creature",),
                    state_predicate=PermanentStatePredicateSpec(attacking=True),
                ),
                "Trample",
            ),
            (
                ObjectQuerySpec(
                    zones=("battlefield",),
                    types_all=("creature",),
                    state_predicate=PermanentStatePredicateSpec(blocking=True),
                ),
                "Vigilance",
            ),
            (
                ObjectQuerySpec(
                    zones=("battlefield",),
                    types_all=("creature",),
                    exclude_ref=source.ref,
                    state_predicate=PermanentStatePredicateSpec(attacking=True),
                ),
                "First Strike",
            ),
        )
        for predicate, keyword in effects:
            engine.apply_effect(
                {
                    "op": "modify_all_matching_permanents_until_end_of_turn",
                    "predicate": predicate.to_dict(),
                    "power": 0,
                    "toughness": 0,
                    "keywords": [keyword],
                },
                actor="A",
            )
        later = self.creature(engine, "B", "Later")

        source_keywords = set(engine._effective_card_data(source)["keywords"])
        ally_keywords = set(engine._effective_card_data(ally)["keywords"])
        b_keywords = set(engine._effective_card_data(opponent_b)["keywords"])
        c_keywords = set(engine._effective_card_data(opponent_c)["keywords"])
        d_keywords = set(engine._effective_card_data(opponent_d)["keywords"])
        self.assertGreaterEqual(source_keywords, {"Flying", "Trample"})
        self.assertNotIn("First Strike", source_keywords)
        self.assertGreaterEqual(
            ally_keywords,
            {"Flying", "Trample", "First Strike"},
        )
        self.assertGreaterEqual(b_keywords, {"Flying", "Menace", "Vigilance"})
        self.assertGreaterEqual(c_keywords, {"Flying", "Menace"})
        self.assertGreaterEqual(d_keywords, {"Flying", "Menace"})
        self.assertFalse(
            {"Flying", "Menace", "Trample", "Vigilance", "First Strike"}
            .intersection(engine._effective_card_data(later)["keywords"])
        )

        engine.change_control(ally.object_id, "B", reason="locked public set")
        self.assertIn("First Strike", engine._effective_card_data(ally)["keywords"])
        engine.move_card(source.object_id, "graveyard", reason="source departure")
        self.assertIn("Menace", engine._effective_card_data(opponent_c)["keywords"])
        engine.move_card(ally.object_id, "graveyard", reason="identity reset")
        engine.move_card(ally.object_id, "battlefield", controller="B", reason="identity reset")
        self.assertNotIn("First Strike", engine._effective_card_data(ally)["keywords"])

        rendered = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:D"),
            sort_keys=True,
        )
        self.assertNotIn(opponent_c.object_id, rendered)
        self.assertNotIn("continuous_effects", rendered)
        self.assertGreater(expire_end_of_turn_continuous_effects(engine.state), 0)
        self.assertNotIn("Menace", engine._effective_card_data(opponent_c)["keywords"])

    def test_target_player_characteristic_set_resolves_and_replays(self):
        session = self.session(6112021, players=4)
        engine = session.engine
        source = self.creature(engine, "A", "Source")
        controlled_b = self.creature(engine, "B", "Controlled B")
        controlled_c = self.creature(engine, "C", "Controlled C")
        compiled = fixed_public_characteristic_set_effect_template(
            "Creatures target player controls gain lifelink until end of turn."
        )
        self.assertIsNotNone(compiled)
        assert compiled is not None
        template_id, effects, target_schema, _mechanics = compiled
        program = SemanticProgram(
            key="test:target-player-public-characteristics",
            label="Target player public characteristics",
            effects=list(effects),
            target_schema=target_schema,
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="target-player-public-characteristics",
                ref="S-target-player-public-characteristics",
                kind="spell",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                source_object_id=source.object_id,
                targets=["B"],
                visibility=list(engine.seats),
                context={
                    "source_logical_object_id": source.logical_object_id,
                    "target_groups": {"target_0": ["B"]},
                    "target_snapshots": {"B": engine._target_snapshot("B")},
                    "targets_revalidated": False,
                    "targets_chosen_at_creation": True,
                    "public_characteristic_template": template_id,
                },
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        self.assertIn(
            "Lifelink",
            engine._effective_card_data(controlled_b)["keywords"],
        )
        self.assertNotIn(
            "Lifelink",
            engine._effective_card_data(controlled_c)["keywords"],
        )
        rendered = json.dumps(session.packet("pilot:D", full=True), sort_keys=True)
        self.assertNotIn(controlled_b.object_id, rendered)
        self.assertNotIn(controlled_b.logical_object_id, rendered)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "target-player-characteristic-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(4, replay["commands"])

    def test_malformed_fixed_characteristic_keywords_roll_back(self):
        for index, keywords in enumerate(
            (["Flying", "Flying"], ["Unsupported"])
        ):
            with self.subTest(keywords=keywords):
                session = self.session(6112011 + index)
                engine = session.engine
                self.creature(engine, "A", "Target")
                before = engine.state.to_dict()
                with self.assertRaisesRegex(
                    Exception,
                    "list|unique supported keywords",
                ):
                    engine.apply_effect(
                        {
                            "op": (
                                "modify_all_matching_permanents_"
                                "until_end_of_turn"
                            ),
                            "predicate": ObjectQuerySpec(
                                zones=("battlefield",),
                                controller="A",
                                types_all=("creature",),
                            ).to_dict(),
                            "power": 1,
                            "toughness": 1,
                            "keywords": keywords,
                        },
                        actor="A",
                    )
                self.assertEqual(before, engine.state.to_dict())

    def test_targeted_effect_expires_and_round_trips_without_private_projection(self):
        session = self.session(6112002)
        engine = session.engine
        creature = self.creature(engine, "A", "Target")
        engine.apply_effect(
            {
                "op": "modify_stats_until_end_of_turn",
                "card": creature.ref,
                "power": 2,
                "toughness": 1,
            },
            actor="A",
        )
        self.assertEqual(3, engine._numeric_stat(creature.object_id, "power"))
        restored = GameState.from_dict(engine.state.to_dict())
        self.assertEqual(
            engine.state.continuous_effects[0].fingerprint,
            restored.continuous_effects[0].fingerprint,
        )
        projected = StateProjector(self.db, engine.state)._snapshot("pilot:B")
        rendered = json.dumps(projected, sort_keys=True)
        self.assertNotIn("continuous_effects", rendered)
        self.assertNotIn(creature.object_id, rendered)
        public_object = next(
            value
            for value in projected["players"]["A"]["bf"]
            if value["id"] == creature.ref
        )
        self.assertEqual("3", public_object["p"])
        self.assertEqual("2", public_object["q"])
        self.assertEqual(1, expire_end_of_turn_continuous_effects(engine.state))
        self.assertEqual(1, engine._numeric_stat(creature.object_id, "power"))

    def test_exact_continuous_effects_compose_while_replacements_stay_residual(
        self,
    ):
        session = self.session(6112006)
        engine = session.engine
        registry = load_default_capability_registry()
        witness_names = (
            "Drogskol Infantry // Drogskol Armaments",
            "Wilt-Leaf Liege",
            "Flamekin Village",
        )
        for name in witness_names:
            compiled = compile_oracle_card(
                self.db.lookup(name),
                capability_registry=registry,
                capability_profile="commander_review",
            )
            blockers = {
                blocker
                for residual in compiled.material_residuals
                for blocker in residual.blockers
            }
            self.assertGreaterEqual(
                blockers,
                {
                    "replacement applicability",
                    "self-replacement and prevention ordering",
                },
            )

        target_ref = engine.create_token(
            "A",
            name="Residual-boundary creature",
            characteristics={
                "type_line": "Token Creature — Spirit",
                "colors": ["G", "W"],
                "power": "2",
                "toughness": "2",
                "keywords": [],
            },
        )[0]
        target = engine._resolve_object(
            "A", target_ref, zones={"battlefield"}
        )
        anthem = self.add_registered_card(
            engine,
            seat="A",
            name="Wilt-Leaf Liege",
            ref="ASSURANCE-ANTHEM",
        )
        aura = self.add_registered_card(
            engine,
            seat="A",
            name="Drogskol Infantry // Drogskol Armaments",
            ref="ASSURANCE-ARMAMENTS",
            active_face="Drogskol Armaments",
        )
        village = self.add_registered_card(
            engine,
            seat="A",
            name="Flamekin Village",
            ref="ASSURANCE-VILLAGE",
        )
        attach_objects(
            engine.state.cards,
            aura,
            target,
            source_timestamp=engine._next_zone_timestamp(),
        )
        engine.apply_effect(
            {
                "op": "grant_keyword_until_end_of_turn",
                "card": target.ref,
                "keyword": "Haste",
            },
            actor="A",
        )

        characteristics = engine._effective_card_data(target)
        self.assertEqual("6", characteristics["power"])
        self.assertEqual("6", characteristics["toughness"])
        self.assertIn("Haste", characteristics["keywords"])
        self.assertEqual("battlefield", anthem.zone)
        self.assertEqual(target.object_id, aura.attached_to)
        self.assertEqual("battlefield", village.zone)

    def test_malformed_mass_predicate_rolls_back_without_effect(self):
        session = self.session(6112003)
        before = session.engine.state.to_dict()
        with self.assertRaisesRegex(Exception, "Object query fields"):
            session.engine.apply_effect(
                {
                    "op": "modify_all_matching_permanents_until_end_of_turn",
                    "predicate": {"zones": ["battlefield"]},
                    "power": 1,
                    "toughness": 1,
                },
                actor="A",
            )
        self.assertEqual(before, session.engine.state.to_dict())

    def test_historical_checkpoint_without_journal_remains_explicitly_legacy(self):
        session = self.session(6112005)
        payload = session.engine.state.to_dict()
        payload.pop("continuous_effects")
        restored = GameState.from_dict(payload)
        self.assertIsNone(restored.continuous_effects)
        self.assertNotIn("continuous_effects", restored.to_dict())

    def test_temporary_effect_command_replays_exactly(self):
        session = self.session(6112004)
        engine = session.engine
        source = self.creature(engine, "A", "Source")
        program = SemanticProgram(
            key="test:locked-temporary-effect",
            label="Locked temporary effect",
            effects=[
                {
                    "op": "modify_stats_until_end_of_turn",
                    "card": "$source",
                    "power": 2,
                    "toughness": 2,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="locked-temporary-effect",
                ref="S-locked-temporary-effect",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                source_object_id=source.object_id,
                visibility=["A", "B"],
                context={
                    "source_logical_object_id": source.logical_object_id
                },
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(3, engine._numeric_stat(source.object_id, "power"))
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "continuous-duration-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])

    def test_fixed_controlled_characteristic_command_replays_exactly(self):
        session = self.session(6112012)
        engine = session.engine
        source = self.creature(engine, "A", "Source")
        target = self.creature(engine, "A", "Target")
        program = SemanticProgram(
            key="test:locked-controlled-characteristics",
            label="Locked controlled characteristics",
            effects=[
                {
                    "op": "modify_all_matching_permanents_until_end_of_turn",
                    "predicate": ObjectQuerySpec(
                        zones=("battlefield",),
                        controller="A",
                        types_all=("creature",),
                        exclude_ref=source.ref,
                    ).to_dict(),
                    "power": 1,
                    "toughness": 1,
                    "keywords": ["Flying"],
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="locked-controlled-characteristics",
                ref="S-locked-controlled-characteristics",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                source_object_id=source.object_id,
                visibility=["A", "B"],
                context={
                    "source_logical_object_id": source.logical_object_id
                },
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        target_data = engine._effective_card_data(target)
        self.assertEqual("2", target_data["power"])
        self.assertIn("Flying", target_data["keywords"])
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "controlled-characteristic-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])


if __name__ == "__main__":
    unittest.main()
