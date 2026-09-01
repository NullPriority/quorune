from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session, pass_current
from quorune.ability_fragments import (
    StaticComponentSpec,
    ability_fragment_to_dict,
)
from quorune.abilities import parse_activated_abilities
from quorune.attachments import (
    AttachmentRelationError,
    attach_objects,
    attached_object_identity,
    detach_object,
)
from quorune.card_programs.runtime import (
    collect_card_program_continuous_effects,
)
from quorune.card_programs.validation import canonical_program_fingerprint
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.continuous_templates import (
    attached_fixed_characteristics_handler,
)
from quorune.compiler.target_effect_corpus_assurance import (
    TargetEffectCorpusCollector,
)
from quorune.continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectError,
    ContinuousEffectOrigin,
    ContinuousEffectRelation,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    CharacteristicState,
    evaluate_continuous_effects,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, CombatState, StackItem
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    AttachedFixedCharacteristicsHandler,
    ContinuousEffectSourceContext,
    SemanticNodeError,
)
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


ROOT = Path(__file__).resolve().parents[1]
ATTACHED_GRANT_FIXTURE = (
    ROOT / "tests" / "fixtures" / "attached-quoted-ability-grants.json"
)


def attached_grant_record(
    oracle_text: str,
    *,
    type_line: str = "Enchantment — Aura",
) -> CardRecord:
    return CardRecord(
        oracle_id="fixture:attached-grant",
        name="Attached Grant Fixture",
        mana_cost="{1}{U}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-09-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class RulinglessCardDatabase:
    @staticmethod
    def rulings(_record):
        return ()


def descriptor(line: str) -> dict:
    compiled = attached_fixed_characteristics_handler(line)
    if compiled is None:
        raise AssertionError(f"fixture did not compile: {line}")
    return copy.deepcopy(dict(compiled[1]))


def card(
    object_id: str,
    *,
    zone: str = "battlefield",
    controller: str = "A",
    zone_change_counter: int = 0,
    zone_timestamp: int = 1,
) -> CardInstance:
    return CardInstance(
        object_id=object_id,
        ref=object_id,
        oracle_id=f"oracle:{object_id}",
        printed_name=object_id,
        owner=controller,
        controller=controller,
        zone=zone,
        zone_change_counter=zone_change_counter,
        zone_timestamp=zone_timestamp,
    )


class AttachmentRelationTests(unittest.TestCase):
    def test_attach_reattach_and_detach_own_reciprocal_timestamped_relation(self):
        source = card("equipment", zone_timestamp=3)
        first = card("first")
        second = card("second")
        cards = {value.object_id: value for value in (source, first, second)}

        first_transition = attach_objects(
            cards, source, first, source_timestamp=7
        )
        self.assertTrue(first_transition.changed)
        self.assertEqual(7, source.zone_timestamp)
        self.assertEqual(first.object_id, source.attached_to)
        self.assertEqual([source.object_id], first.attachments)
        self.assertEqual(
            ContinuousObjectIdentity(
                object_id=first.object_id,
                logical_object_id=first.logical_object_id,
            ),
            attached_object_identity(cards, source),
        )

        second_transition = attach_objects(
            cards, source, second, source_timestamp=11
        )
        self.assertEqual(first.object_id, second_transition.previous_target_id)
        self.assertEqual(11, source.zone_timestamp)
        self.assertEqual([], first.attachments)
        self.assertEqual([source.object_id], second.attachments)

        detached = detach_object(cards, source)
        self.assertTrue(detached.changed)
        self.assertIsNone(source.attached_to)
        self.assertEqual([], second.attachments)
        self.assertEqual(11, source.zone_timestamp)

    def test_reattaching_to_same_object_preserves_timestamp(self):
        source = card("aura", zone_timestamp=3)
        target = card("target")
        cards = {source.object_id: source, target.object_id: target}
        attach_objects(cards, source, target, source_timestamp=7)
        transition = attach_objects(
            cards, source, target, source_timestamp=99
        )
        self.assertFalse(transition.changed)
        self.assertEqual(7, source.zone_timestamp)

    def test_malformed_relation_fails_before_mutation(self):
        source = card("equipment")
        previous = card("previous")
        target = card("target")
        cards = {value.object_id: value for value in (source, previous, target)}
        source.attached_to = previous.object_id
        before = {
            key: value.to_dict() for key, value in cards.items()
        }
        with self.assertRaisesRegex(
            AttachmentRelationError, "not reciprocal"
        ):
            attach_objects(cards, source, target, source_timestamp=9)
        self.assertEqual(
            before, {key: value.to_dict() for key, value in cards.items()}
        )

    def test_identity_requires_live_reciprocal_phased_in_battlefield_objects(self):
        source = card("aura")
        target = card("target")
        cards = {source.object_id: source, target.object_id: target}
        source.attached_to = target.object_id
        self.assertIsNone(attached_object_identity(cards, source))
        target.attachments.append(source.object_id)
        self.assertIsNotNone(attached_object_identity(cards, source))
        target.phased_out = True
        self.assertIsNone(attached_object_identity(cards, source))


class AttachedContinuousModelTests(unittest.TestCase):
    @staticmethod
    def context(
        *, logical_object_id: str = "target@0"
    ) -> ContinuousEffectSourceContext:
        return ContinuousEffectSourceContext(
            source_object_id="source",
            source_ref="S1",
            source_controller="A",
            source_timestamp=5,
            component_id="test:attached:0",
            attached_object=ContinuousObjectIdentity(
                object_id="target",
                logical_object_id=logical_object_id,
            ),
        )

    @staticmethod
    def base() -> CharacteristicState:
        return CharacteristicState(
            name="Target",
            controller="B",
            card_types={"Creature"},
            subtypes={"Human"},
            abilities=[],
            power=1,
            toughness=1,
        )

    def test_fixed_modifier_and_keyword_apply_only_to_exact_logical_object(self):
        effects = AttachedFixedCharacteristicsHandler().lower(
            descriptor(
                "Equipped creature gets +2/+1 and has flying and vigilance."
            ),
            self.context(),
        )
        applied = evaluate_continuous_effects(
            self.base(),
            effects,
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "zone": "battlefield",
                "owner": "B",
            },
        )
        self.assertEqual(3, applied.characteristics["power"])
        self.assertEqual(2, applied.characteristics["toughness"])
        self.assertEqual(
            {"Flying", "Vigilance"},
            set(applied.characteristics["abilities"]),
        )

        returned = evaluate_continuous_effects(
            self.base(),
            effects,
            context={
                "object_id": "target",
                "logical_object_id": "target@1",
                "zone": "battlefield",
                "owner": "B",
            },
        )
        self.assertEqual(1, returned.characteristics["power"])
        self.assertFalse(returned.applied_effects)

    def test_attached_subject_type_is_rechecked_before_modifier_layers(self):
        descriptor_value = descriptor(
            "Enchanted creature gets -4/-0."
        )
        self.assertEqual(
            ["creature"],
            descriptor_value["condition"]["types_all"],
        )
        effects = AttachedFixedCharacteristicsHandler().lower(
            descriptor_value,
            self.context(),
        )
        vehicle = CharacteristicState(
            name="Vehicle",
            controller="B",
            card_types={"Artifact"},
            subtypes={"Vehicle"},
            abilities=[],
            power=4,
            toughness=4,
        )
        result = evaluate_continuous_effects(
            vehicle,
            effects,
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "zone": "battlefield",
                "owner": "B",
            },
        )
        self.assertEqual(4, result.characteristics["power"])
        self.assertFalse(result.applied_effects)

        legacy = copy.deepcopy(descriptor_value)
        legacy["condition"].pop("types_all")
        legacy_effects = AttachedFixedCharacteristicsHandler().lower(
            legacy,
            self.context(),
        )
        legacy_result = evaluate_continuous_effects(
            vehicle,
            legacy_effects,
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "zone": "battlefield",
                "owner": "B",
            },
        )
        self.assertEqual(0, legacy_result.characteristics["power"])

    def test_type_and_ability_removal_use_their_canonical_layers(self):
        type_effect = AttachedFixedCharacteristicsHandler().lower(
            descriptor(
                "Enchanted creature is a Zombie in addition to its other types."
            ),
            self.context(),
        )
        removal = AttachedFixedCharacteristicsHandler().lower(
            descriptor("Enchanted creature loses flying."),
            ContinuousEffectSourceContext(
                source_object_id="second-source",
                source_ref="S2",
                source_controller="C",
                source_timestamp=8,
                component_id="test:attached:1",
                attached_object=self.context().attached_object,
            ),
        )
        base = self.base()
        base.abilities.append("Flying")
        result = evaluate_continuous_effects(
            base,
            (*type_effect, *removal),
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "zone": "battlefield",
                "owner": "B",
            },
        )
        self.assertEqual({"human", "zombie"}, {
            value.casefold() for value in result.characteristics["subtypes"]
        })
        self.assertNotIn("Flying", result.characteristics["abilities"])

    def test_dynamic_transform_and_remove_then_grant_use_canonical_layers(self):
        dynamic = AttachedFixedCharacteristicsHandler().lower(
            descriptor(
                "Enchanted creature gets +1/+1 for each artifact you control."
            ),
            replace(self.context(), resolved_quantity=3),
        )
        scaled = evaluate_continuous_effects(
            self.base(),
            dynamic,
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "zone": "battlefield",
                "owner": "B",
            },
        )
        self.assertEqual(4, scaled.characteristics["power"])
        self.assertEqual(4, scaled.characteristics["toughness"])

        transformed = AttachedFixedCharacteristicsHandler().lower(
            descriptor(
                "Enchanted creature loses all abilities and is a blue Frog "
                "creature with base power and toughness 1/1."
            ),
            self.context(),
        )
        base = self.base()
        base.card_types.add("Artifact")
        base.colors.add("R")
        base.abilities.append("Flying")
        frog = evaluate_continuous_effects(
            base,
            transformed,
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "zone": "battlefield",
                "owner": "B",
            },
        ).characteristics
        self.assertEqual(["Creature"], frog["card_types"])
        self.assertEqual(["Frog"], frog["subtypes"])
        self.assertEqual(["U"], frog["colors"])
        self.assertEqual([], frog["abilities"])
        self.assertEqual((1, 1), (frog["power"], frog["toughness"]))

        darksteel = AttachedFixedCharacteristicsHandler().lower(
            descriptor(
                "Enchanted creature is an Insect artifact creature with base "
                "power and toughness 0/1 and has indestructible, and it loses "
                "all other abilities, card types, and creature types."
            ),
            self.context(),
        )
        mutation = evaluate_continuous_effects(
            self.base(),
            darksteel,
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "zone": "battlefield",
                "owner": "B",
            },
        ).characteristics
        self.assertEqual({"Artifact", "Creature"}, set(mutation["card_types"]))
        self.assertEqual(["Insect"], mutation["subtypes"])
        self.assertEqual(["Indestructible"], mutation["abilities"])
        self.assertEqual((0, 1), (mutation["power"], mutation["toughness"]))

        all_types_base = self.base()
        all_types_base.subtypes.add("Equipment")
        all_types = evaluate_continuous_effects(
            all_types_base,
            AttachedFixedCharacteristicsHandler().lower(
                descriptor(
                    "Equipped creature gets +1/+1 and is every creature type."
                ),
                self.context(),
            ),
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "zone": "battlefield",
                "owner": "B",
            },
        ).characteristics["subtypes"]
        self.assertIn("Equipment", all_types)
        self.assertIn("Goblin", all_types)

    def test_attached_fixed_ward_uses_current_fragment(self):
        effects = AttachedFixedCharacteristicsHandler().lower(
            descriptor(
                "Enchanted creature gets +3/+3 and has ward {2}."
            ),
            self.context(),
        )
        result = evaluate_continuous_effects(
            self.base(),
            effects,
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "zone": "battlefield",
                "owner": "B",
            },
        )
        self.assertEqual(4, result.characteristics["power"])
        self.assertEqual(["Ward {2}"], result.characteristics["abilities"])
        self.assertEqual(
            [
                {
                    "kind": "ward",
                    "value": {"schema_version": 1, "generic_cost": 2},
                }
            ],
            result.characteristics["ability_fragments"],
        )

    def test_relation_round_trip_is_canonical_and_legacy_shape_stays_stable(self):
        effect = AttachedFixedCharacteristicsHandler().lower(
            descriptor("Enchanted creature gets -1/-0."),
            self.context(),
        )[0]
        payload = effect.to_dict()
        self.assertEqual(
            "source_attached_to_object", payload["relation"]
        )
        restored = ContinuousEffect.from_dict(payload)
        self.assertEqual(effect.fingerprint, restored.fingerprint)

        legacy = dict(payload)
        legacy.pop("relation")
        legacy.pop("related_object")
        legacy["origin"] = "object"
        legacy_effect = ContinuousEffect.from_dict(legacy)
        self.assertEqual(legacy, legacy_effect.to_dict())

    def test_relation_model_and_descriptor_fail_closed(self):
        effect = AttachedFixedCharacteristicsHandler().lower(
            descriptor("Equipped creature has shroud."),
            self.context(),
        )[0]
        malformed = effect.to_dict()
        malformed["related_object"] = None
        with self.assertRaises(ContinuousEffectError):
            ContinuousEffect.from_dict(malformed)

        bad_descriptor = descriptor("Equipped creature has shroud.")
        bad_descriptor["modifier"]["power"] = True
        with self.assertRaisesRegex(SemanticNodeError, "integers"):
            AttachedFixedCharacteristicsHandler().validate(bad_descriptor)

        unrelated = copy.deepcopy(effect.to_dict())
        unrelated["relation"] = ContinuousEffectRelation.NONE.value
        with self.assertRaisesRegex(
            ContinuousEffectError, "cannot name a related object"
        ):
            ContinuousEffect.from_dict(unrelated)

    def test_unattached_source_lowers_no_effect(self):
        effects = AttachedFixedCharacteristicsHandler().lower(
            descriptor("Equipped creature has haste."),
            ContinuousEffectSourceContext(
                source_object_id="source",
                source_ref="S1",
                source_controller="A",
                source_timestamp=5,
                component_id="test:unattached",
            ),
        )
        self.assertEqual((), effects)


class AttachedContinuousCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _, _ = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_compiler_lowers_fixed_attached_characteristic_families(self):
        fixtures = {
            "Enchanted creature gets +1/+1.": (1, 1, [], []),
            "Equipped creature has haste and shroud.": (
                0,
                0,
                ["Haste", "Shroud"],
                [],
            ),
            "Fortified land loses indestructible.": (
                0,
                0,
                [],
                ["Indestructible"],
            ),
        }
        for line, expected in fixtures.items():
            with self.subTest(line=line):
                value = descriptor(line)["modifier"]
                self.assertEqual(expected[0], value["power"])
                self.assertEqual(expected[1], value["toughness"])
                self.assertEqual(expected[2], value["add_abilities"])
                self.assertEqual(expected[3], value["remove_abilities"])

    def test_compiler_rejects_conditional_and_unrepresented_text(self):
        for line in (
            "Equipped creature gets +X/+X.",
            "Enchanted creature can't attack.",
            'Equipped creature has "{T}: Draw a card."',
            "As long as enchanted creature is red, it gets +1/+1.",
            (
                "Enchanted creature loses all abilities and is a green and "
                "white Citizen creature with base power and toughness 1/1 "
                "named Legitimate Businessperson."
            ),
            (
                "Enchanted creature is a Turtle with base power and toughness "
                "0/1. It can't attack and loses all abilities."
            ),
            (
                "Equipped creature gets +1/+0 for each Equipment attached to it."
            ),
        ):
            with self.subTest(line=line):
                self.assertIsNone(
                    attached_fixed_characteristics_handler(line)
                )

    def test_compiler_lowers_closed_dynamic_compound_and_transform_forms(self):
        fixtures = {
            "Equipped creature gets +1/+0 for each Gate you control and has vigilance and menace.": (
                1,
                0,
                ["Vigilance", "Menace"],
            ),
            "Enchanted creature gets +2/+2, has first strike, and is a Knight in addition to its other types.": (
                0,
                0,
                ["First Strike"],
            ),
            "Equipped creature has base power and toughness 10/10.": (
                0,
                0,
                [],
            ),
        }
        for line, expected in fixtures.items():
            with self.subTest(line=line):
                modifier = descriptor(line)["modifier"]
                self.assertEqual(expected[0], modifier["quantity_power"])
                self.assertEqual(expected[1], modifier["quantity_toughness"])
                self.assertEqual(expected[2], modifier["add_abilities"])

        other = descriptor(
            "Enchanted creature gets +1/+1 for each other creature you control."
        )["modifier"]["quantity"]
        self.assertFalse(other["exclude_source"])
        self.assertTrue(other["exclude_attached_object"])

    def test_measured_attached_characteristic_cohort_is_capability_closed(self):
        fixture = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "attached-characteristic-closure-cards.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(69, len(fixture["cards"]))
        capabilities = load_default_capability_registry()
        for card_data in fixture["cards"]:
            name = card_data["name"]
            with self.subTest(name=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        descriptor.get("handler_id")
                        == "continuous.attached.fixed-characteristics.v1"
                        for face in ir.faces
                        for node in face.nodes
                        for descriptor in node.handlers
                    )
                )

    def test_compiler_lowers_granted_protection_to_a_typed_fragment(self):
        result = attached_fixed_characteristics_handler(
            "Enchanted creature has protection from red."
        )
        self.assertIsNotNone(result)
        modifier = result[1]["modifier"]
        self.assertEqual(["Protection"], modifier["add_abilities"])
        self.assertEqual([], modifier["add_rules_text"])
        self.assertEqual(
            [
                {
                    "kind": "protection",
                    "value": {
                        "schema_version": 1,
                        "quality_kind": "color",
                        "quality": "R",
                    },
                }
            ],
            modifier["add_ability_fragments"],
        )

    def test_compiler_lowers_coupled_protection_and_fixed_ward_grants(self):
        protection = attached_fixed_characteristics_handler(
            (
                "Equipped creature gets +2/+2 and has protection from red "
                "and from blue."
            )
        )
        self.assertIsNotNone(protection)
        self.assertEqual(
            ["Protection"], protection[1]["modifier"]["add_abilities"]
        )
        self.assertEqual(
            2,
            len(protection[1]["modifier"]["add_ability_fragments"]),
        )
        self.assertIn("protection.typed.debt", protection[2])

        ward = attached_fixed_characteristics_handler(
            (
                "Enchanted creature gets +3/+3 and has ward {2}. "
                "(Whenever it becomes the target of a spell or ability an "
                "opponent controls, counter it unless that player pays {2}.)"
            )
        )
        self.assertIsNotNone(ward)
        self.assertEqual(
            ["Ward {2}"], ward[1]["modifier"]["add_abilities"]
        )
        self.assertEqual(
            [
                {
                    "kind": "ward",
                    "value": {"schema_version": 1, "generic_cost": 2},
                }
            ],
            ward[1]["modifier"]["add_ability_fragments"],
        )
        self.assertIn("trigger.keyword.ward.fixed_generic", ward[2])
        self.assertIsNone(
            attached_fixed_characteristics_handler(
                "Enchanted creature gets +3/+3 and has ward—Pay 3 life."
            )
        )

    def test_compiler_lowers_granted_toxic_to_a_typed_fragment(self):
        result = attached_fixed_characteristics_handler(
            "Equipped creature has toxic 2."
        )
        self.assertIsNotNone(result)
        modifier = result[1]["modifier"]
        self.assertEqual(["Toxic 2"], modifier["add_abilities"])
        self.assertEqual(
            [
                {
                    "kind": "toxic",
                    "value": {"schema_version": 1, "value": 2},
                }
            ],
            modifier["add_ability_fragments"],
        )
        self.assertIsNone(
            attached_fixed_characteristics_handler(
                "Equipped creature loses toxic 2."
            )
        )

    def test_compiler_lowers_attached_quoted_abilities_to_typed_programs(self):
        fixtures = (
            (
                'Enchanted creature has "{T}: This creature deals 1 damage '
                'to any target."',
                "Enchantment — Aura",
                "granted_activated_ability",
                "granted_activated",
            ),
            (
                'Equipped creature gets +1/+1 and has "Whenever this '
                'creature attacks, you gain 2 life."',
                "Artifact — Equipment",
                "granted_triggered_ability",
                "granted_triggered",
            ),
            (
                'Enchanted creature has "{T}: Add {G}."',
                "Enchantment — Aura",
                "granted_mana_ability",
                "granted_activated",
            ),
        )
        capabilities = load_default_capability_registry()
        for text, type_line, inner_kind, fragment_kind in fixtures:
            with self.subTest(text=text):
                record = attached_grant_record(text, type_line=type_line)
                ir = compile_oracle_card(
                    record,
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.to_dict())
                TargetEffectCorpusCollector().observe(record, ir)
                outer = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id
                    == "continuous-attached-fixed-characteristics-"
                    "granted-ability-v1"
                )
                inner = next(
                    node for node in ir.faces[0].nodes if node.kind == inner_kind
                )
                fragment = outer.handlers[0]["modifier"][
                    "add_ability_fragments"
                ][0]
                self.assertEqual(fragment_kind, fragment["kind"])
                self.assertEqual(
                    inner.text,
                    text[text.index('"') + 1 : text.rindex('"')],
                )
                self.assertNotIn("Flying", outer.handlers[0]["modifier"]["add_abilities"])

                programs = generated_programs(
                    RulinglessCardDatabase(),
                    record,
                    trust_level="trusted",
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                inner_program = next(
                    program
                    for program in programs
                    if program.ability_id == fragment["value"]["ability_id"]
                )
                self.assertEqual(inner_program.key, fragment["value"]["semantic_key"])
                self.assertTrue(inner_program.provenance["granted_only"])
                self.assertEqual("battlefield", inner_program.active_zone)

    def test_compiler_keeps_unsupported_attached_grants_residual(self):
        fixtures = (
            'Enchanted creature has "Sacrifice this creature: You gain 2 life."',
            'Enchanted creature has "Discard a card: Draw a card."',
            'Enchanted creature has "Pay 2 life: Draw a card."',
            'Enchanted creature has "This creature has flying."',
            'Enchanted creature has "{T}: Tap this Aura."',
            'Enchanted creature has "{T}: Draw a card." and "{T}: Add {G}."',
        )
        capabilities = load_default_capability_registry()
        for text in fixtures:
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    attached_grant_record(text),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertFalse(
                    any(
                        node.kind.startswith("granted_")
                        for node in ir.faces[0].nodes
                    )
                )

    def test_fixed_equip_keyword_has_exact_bounded_capability(self):
        greaves = self.db.lookup("Lightning Greaves")
        ir = compile_oracle_card(
            greaves,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        equip = next(
            node
            for node in ir.faces[0].nodes
            if node.text.casefold().startswith("equip ")
        )
        self.assertTrue(equip.exact)
        self.assertEqual(
            ("attachment.equip.fixed_mana",),
            equip.capability_dependencies,
        )

        variable = replace(
            greaves, oracle_text="Equip {X}", keywords=("Equip",)
        )
        unresolved = compile_oracle_card(
            variable,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        self.assertEqual("partial", unresolved.status)
        self.assertIn(
            "mechanic:equip",
            next(
                residual
                for residual in unresolved.material_residuals
                if residual.text == "Equip {X}"
            ).blockers,
        )

    def test_fixed_equip_parser_exposes_only_executable_target_contract(self):
        ability = parse_activated_abilities(
            card_name="Review Equipment",
            oracle_text="Equip {2}",
            keywords=("Equip",),
        )[0]
        self.assertTrue(ability.compiled_cost)
        self.assertTrue(ability.sorcery_speed)
        self.assertEqual("builtin:equip", ability.builtin_semantic_key)
        self.assertEqual(
            {
                "zones": ("battlefield",),
                "categories": ("permanent",),
                "controller": "you",
                "creature": True,
                "count": 1,
            },
            dict(ability.target_schema),
        )

        variable = parse_activated_abilities(
            card_name="Unresolved Equipment",
            oracle_text="Equip {X}",
            keywords=("Equip",),
        )[0]
        self.assertFalse(variable.compiled_cost)


class AttachedStaticEngineTests(unittest.TestCase):
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
        return session

    @staticmethod
    def find(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def put_on_battlefield(self, engine, *cards):
        for value in cards:
            engine.move_card(
                value.object_id,
                "battlefield",
                controller=value.controller,
                log=False,
            )

    def test_static_component_applicability_batches_battlefield_scan(self):
        session = self.session(6131008)
        engine = session.engine
        for index in range(12):
            engine.create_token(
                "A",
                name=f"Component Presence Witness {index}",
                characteristics={
                    "type_line": "Token Artifact Creature — Thopter",
                    "power": "1",
                    "toughness": "1",
                    "keywords": [],
                },
                reason="static-component batching assurance",
            )

        with mock.patch(
            "quorune.characteristic_evaluation_host."
            "collect_card_program_continuous_effects",
            wraps=collect_card_program_continuous_effects,
        ) as collector:
            component_keys = engine._effective_static_component_key_map()

        self.assertEqual(1, collector.call_count)
        self.assertEqual(12, len(component_keys))
        self.assertTrue(all(not keys for keys in component_keys.values()))

    def test_current_semantic_trust_cache_tracks_registry_identity(self):
        session = self.session(6131009)
        engine = session.engine
        record = self.db.lookup("Lightning Greaves")
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        program = next(
            value
            for value in engine.semantics.programs_for_oracle(
                record.oracle_id
            )
            if value.ability_id.startswith("static:")
        )
        engine._semantic_trust_cache.clear()
        engine._current_semantic_trust_cache.clear()

        with mock.patch(
            "quorune.engine.canonical_program_fingerprint",
            wraps=canonical_program_fingerprint,
        ) as fingerprint:
            self.assertTrue(engine.semantic_program_is_current_trusted(program))
            self.assertTrue(engine.semantic_program_is_current_trusted(program))
            self.assertEqual(1, fingerprint.call_count)

            replacement = replace(program, label=f"{program.label} updated")
            engine.semantics.put(replacement)
            self.assertTrue(
                engine.semantic_program_is_current_trusted(replacement)
            )
            self.assertEqual(2, fingerprint.call_count)

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
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
            object_id=f"attachment-assurance:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=(
                [seat]
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
            revealed_to=(
                []
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
        )
        engine.state.cards[value.object_id] = value
        engine.state.players[seat].zones[zone].append(value.object_id)
        return value

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def test_generic_fixed_equip_activation_attaches_and_applies_characteristics(self):
        session = self.session(6131000)
        engine = session.engine
        greaves = self.find(engine, "A", "Lightning Greaves")
        mishra = self.find(engine, "A", "Mishra, Eminent One")
        opponent = self.find(engine, "B", "Zimone and Dina")
        self.put_on_battlefield(engine, greaves, mishra, opponent)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"

        with self.assertRaisesRegex(GameRuleError, "target"):
            engine._activate(
                "A",
                {
                    "source": greaves.ref,
                    "ability": "ab2",
                    "targets": [opponent.ref],
                },
            )
        self.assertIsNone(greaves.attached_to)

        engine._activate(
            "A",
            {
                "source": greaves.ref,
                "ability": "ab2",
                "targets": [mishra.ref],
            },
        )
        self.assertEqual("builtin:equip", engine.state.stack[-1].semantic_key)
        self.resolve_top(engine)
        self.assertEqual(mishra.object_id, greaves.attached_to)
        self.assertEqual(
            {"Haste", "Shroud"},
            {"Haste", "Shroud"}.intersection(
                engine._effective_card_data(mishra)["keywords"]
            ),
        )

    def test_live_generic_equipment_effect_moves_detaches_and_tracks_control(self):
        session = self.session(6131001)
        engine = session.engine
        greaves = self.find(engine, "A", "Lightning Greaves")
        mishra = self.find(engine, "A", "Mishra, Eminent One")
        engineer = self.find(engine, "A", "Goblin Engineer")
        self.put_on_battlefield(engine, greaves, mishra, engineer)

        attach_objects(
            engine.state.cards,
            greaves,
            mishra,
            source_timestamp=engine._next_zone_timestamp(),
        )
        self.assertEqual(
            {"Haste", "Shroud"},
            {"Haste", "Shroud"}.intersection(
                engine._effective_card_data(mishra)["keywords"]
            ),
        )
        engine.change_control(
            mishra.object_id, "B", reason="attached target control witness"
        )
        self.assertIn(
            "Shroud", engine._effective_card_data(mishra)["keywords"]
        )
        engine.change_control(
            greaves.object_id, "B", reason="attachment control witness"
        )
        self.assertIn(
            "Shroud", engine._effective_card_data(mishra)["keywords"]
        )

        first_timestamp = greaves.zone_timestamp
        attach_objects(
            engine.state.cards,
            greaves,
            engineer,
            source_timestamp=engine._next_zone_timestamp(),
        )
        self.assertGreater(greaves.zone_timestamp, first_timestamp)
        self.assertNotIn(
            "Shroud", engine._effective_card_data(mishra)["keywords"]
        )
        self.assertIn(
            "Shroud", engine._effective_card_data(engineer)["keywords"]
        )
        detach_object(engine.state.cards, greaves)
        self.assertNotIn(
            "Shroud", engine._effective_card_data(engineer)["keywords"]
        )

    def test_shared_static_component_query_owns_removal_and_typed_grant(self):
        session = self.session(6131007)
        engine = session.engine
        greaves = self.find(engine, "A", "Lightning Greaves")
        first = self.find(engine, "A", "Mishra, Eminent One")
        second = self.find(engine, "A", "Goblin Engineer")
        self.put_on_battlefield(engine, greaves, first, second)
        attach_objects(
            engine.state.cards,
            greaves,
            first,
            source_timestamp=engine._next_zone_timestamp(),
        )
        program = next(
            value
            for value in engine.semantics.runtime_handler_programs_for_oracle(
                greaves.oracle_id,
                active_zone="battlefield",
                event="characteristics.evaluate",
            )
            if value.ability_id.startswith("static:")
        )
        self.assertIn(
            program.key,
            engine._effective_static_component_keys(greaves),
        )
        self.assertIn("Haste", engine._effective_card_data(first)["keywords"])

        removal = ContinuousEffect(
            effect_id="test:remove-static-component",
            source_id="test:removal",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=engine._next_zone_timestamp(),
            operations=(ContinuousOperation("remove_all_abilities"),),
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(
                ContinuousObjectIdentity(
                    object_id=greaves.object_id,
                    logical_object_id=greaves.logical_object_id,
                ),
            ),
        )
        commit_continuous_effect(engine.state, removal)
        self.assertNotIn(
            program.key,
            engine._effective_static_component_keys(greaves),
        )
        self.assertNotIn(
            "Haste", engine._effective_card_data(first)["keywords"]
        )

        granted = card("granted-equipment", zone_timestamp=99)
        granted.oracle_id = second.oracle_id
        granted.annotations["object_characteristics"] = {
            "name": "Granted Equipment",
            "type_line": "Artifact — Equipment",
            "oracle_text": "",
            "display_text": "",
            "keywords": [],
            "colors": [],
            "ability_fragments": [],
        }
        granted.annotations["granted_ability_fragments"] = [
            ability_fragment_to_dict(StaticComponentSpec(program.key))
        ]
        engine.state.cards[granted.object_id] = granted
        engine.state.players["A"].zones["battlefield"].append(
            granted.object_id
        )
        attach_objects(
            engine.state.cards,
            granted,
            second,
            source_timestamp=engine._next_zone_timestamp(),
        )
        self.assertEqual(
            {"Haste", "Shroud"},
            {"Haste", "Shroud"}.intersection(
                engine._effective_card_data(second)["keywords"]
            ),
        )
        granted.annotations["granted_ability_fragments"] = [
            ability_fragment_to_dict(
                StaticComponentSpec("missing:static:component")
            )
        ]
        self.assertNotIn(
            "Haste", engine._effective_card_data(second)["keywords"]
        )

    def test_dynamic_quantities_use_source_controller_and_attached_exclusion(self):
        session = self.session(6131008)
        engine = session.engine
        target = self.find(engine, "A", "Mishra, Eminent One")
        other = self.find(engine, "A", "Goblin Engineer")
        self.put_on_battlefield(engine, target)
        base_power = engine._numeric_stat(target.object_id, "power")

        glitter = self.add_card(
            engine,
            seat="A",
            name="All That Glitters",
            ref="DYNAMIC-GLITTER",
            zone="battlefield",
        )
        attach_objects(
            engine.state.cards,
            glitter,
            target,
            source_timestamp=engine._next_zone_timestamp(),
        )
        self.assertEqual(
            base_power + 1,
            engine._numeric_stat(target.object_id, "power"),
        )
        engine.create_token(
            "A",
            name="Counted Clue",
            characteristics={
                "type_line": "Token Artifact — Clue",
                "colors": [],
                "keywords": [],
            },
        )
        self.assertEqual(
            base_power + 2,
            engine._numeric_stat(target.object_id, "power"),
        )
        engine.change_control(
            glitter.object_id,
            "B",
            reason="dynamic attached source-controller witness",
        )
        self.assertEqual(
            base_power + 1,
            engine._numeric_stat(target.object_id, "power"),
        )

        engine.move_card(glitter.object_id, "graveyard", log=False)
        bravado = self.add_card(
            engine,
            seat="A",
            name="Bravado",
            ref="DYNAMIC-VAMPIRISM",
            zone="battlefield",
        )
        attach_objects(
            engine.state.cards,
            bravado,
            target,
            source_timestamp=engine._next_zone_timestamp(),
        )
        self.assertEqual(
            base_power,
            engine._numeric_stat(target.object_id, "power"),
        )
        self.put_on_battlefield(engine, other)
        self.assertEqual(
            base_power + 1,
            engine._numeric_stat(target.object_id, "power"),
        )

    def test_animate_dead_and_bestow_modifiers_use_generic_attached_handler(self):
        session = self.session(6131002)
        engine = session.engine
        aura = self.find(engine, "B", "Animate Dead")
        nantuko = self.find(engine, "B", "Springheart Nantuko")
        target = self.find(engine, "B", "Birds of Paradise")
        self.put_on_battlefield(engine, nantuko, target)
        # Animate Dead's graveyard-card restriction is intentionally outside
        # the bounded simple-object Aura grammar.  Preserve this legacy
        # semantic-component fixture by supplying its already-selected target.
        aura.annotations["pending_aura_target"] = target.ref
        self.put_on_battlefield(engine, aura)
        nantuko.annotations["bestowed"] = True

        attach_objects(
            engine.state.cards,
            aura,
            target,
            source_timestamp=engine._next_zone_timestamp(),
        )
        attach_objects(
            engine.state.cards,
            nantuko,
            target,
            source_timestamp=engine._next_zone_timestamp(),
        )
        self.assertEqual(0, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(2, engine._numeric_stat(target.object_id, "toughness"))

        aura.phased_out = True
        self.assertEqual(1, engine._numeric_stat(target.object_id, "power"))
        target.phased_out = True
        self.assertIsNone(
            attached_object_identity(engine.state.cards, nantuko)
        )

    def test_target_departure_and_source_departure_end_effect_without_stale_identity(self):
        session = self.session(6131003)
        engine = session.engine
        greaves = self.find(engine, "A", "Lightning Greaves")
        target = self.find(engine, "A", "Goblin Engineer")
        self.put_on_battlefield(engine, greaves, target)
        attach_objects(
            engine.state.cards,
            greaves,
            target,
            source_timestamp=engine._next_zone_timestamp(),
        )
        self.assertIn(
            "Haste", engine._effective_card_data(target)["keywords"]
        )
        engine.move_card(target.object_id, "graveyard", log=False)
        engine.move_card(
            target.object_id, "battlefield", controller="A", log=False
        )
        self.assertIsNone(greaves.attached_to)
        self.assertNotIn(
            "Haste", engine._effective_card_data(target)["keywords"]
        )
        attach_objects(
            engine.state.cards,
            greaves,
            target,
            source_timestamp=engine._next_zone_timestamp(),
        )
        engine.move_card(greaves.object_id, "graveyard", log=False)
        self.assertNotIn(
            "Haste", engine._effective_card_data(target)["keywords"]
        )

    def test_multiple_sources_apply_independently_in_four_player_game(self):
        session = self.session(6131004, players=4)
        engine = session.engine
        greaves = self.find(engine, "A", "Lightning Greaves")
        skullclamp = self.find(engine, "D", "Skullclamp")
        target = self.find(engine, "B", "Zimone and Dina")
        self.put_on_battlefield(engine, greaves, skullclamp, target)
        for source in (greaves, skullclamp):
            attach_objects(
                engine.state.cards,
                source,
                target,
                source_timestamp=engine._next_zone_timestamp(),
            )
        data = engine._effective_card_data(target)
        self.assertEqual("4", data["power"])
        self.assertEqual("3", data["toughness"])
        self.assertIn("Shroud", data["keywords"])

    def test_aura_equipment_anthem_temporary_effect_and_draw_compose(self):
        session = self.session(6131006)
        engine = session.engine
        target_ref = engine.create_token(
            "A",
            name="Assurance Thopter",
            characteristics={
                "type_line": "Token Artifact Creature — Thopter",
                "colors": ["B"],
                "power": "3",
                "toughness": "3",
                "keywords": [],
            },
        )[0]
        target = engine._resolve_object(
            "A", target_ref, zones={"battlefield"}
        )
        anthem = self.find(engine, "A", "Stridehangar Automaton")
        self.put_on_battlefield(engine, anthem)
        equipment = self.add_card(
            engine,
            seat="A",
            name="Skullclamp",
            ref="ASSURANCE-EQUIPMENT",
            zone="battlefield",
        )
        aura = self.add_card(
            engine,
            seat="A",
            name="Scavenged Weaponry",
            ref="ASSURANCE-AURA",
            zone="hand",
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine.state.players["A"].mana_pool["C"] = 1

        engine._activate(
            "A",
            {
                "source": equipment.ref,
                "ability": "ab3",
                "targets": [target.ref],
            },
        )
        self.resolve_top(engine)
        self.assertEqual(target.object_id, equipment.attached_to)

        engine.apply_effect(
            {
                "op": "modify_stats_until_end_of_turn",
                "card": target.ref,
                "power": 1,
                "toughness": 1,
            },
            actor="A",
        )
        engine.state.players["A"].mana_pool["B"] = 3
        library_before = len(engine.state.players["A"].zones["library"])
        engine._cast(
            "A",
            {
                "card": aura.ref,
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"B": 3},
            },
        )
        self.resolve_top(engine)
        self.assertEqual("battlefield", aura.zone)
        self.assertEqual(target.object_id, aura.attached_to)
        self.assertTrue(engine.state.stack)
        for _ in range(3):
            if not engine.state.stack:
                break
            self.resolve_top(engine)
        engine.apply_effect(
            {"op": "draw", "player": "A", "count": 1},
            actor="A",
        )

        characteristics = engine._effective_card_data(target)
        self.assertEqual("7", characteristics["power"])
        self.assertEqual("5", characteristics["toughness"])
        self.assertEqual(
            library_before - 1,
            len(engine.state.players["A"].zones["library"]),
        )

    def test_projection_hides_relation_identity_and_replay_is_exact(self):
        session = self.session(6131005)
        engine = session.engine
        greaves = self.find(engine, "A", "Lightning Greaves")
        target = self.find(engine, "A", "Mishra, Eminent One")
        self.put_on_battlefield(engine, greaves, target)
        program = SemanticProgram(
            key="test:attached-replay",
            label="Attach replay witness",
            effects=[
                {
                    "op": "attach",
                    "equipment": greaves.ref,
                    "creature": target.ref,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="attached-replay",
                ref="S-attached-replay",
                kind="activated_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                source_object_id=greaves.object_id,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(target.object_id, greaves.attached_to)
        projected = session.projector._snapshot("pilot:B")
        rendered = json.dumps(projected, sort_keys=True)
        self.assertNotIn("continuous_effects", rendered)
        self.assertNotIn(target.object_id, rendered)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "attached-static-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])


class AttachedQuotedAbilityRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "attached-grants.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ATTACHED_GRANT_FIXTURE,
            ],
            database,
        )
        cls.db = CardDatabase(database)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        for name in (
            "Attached Grant Damage Fixture",
            "Attached Grant Trigger Fixture",
            "Attached Grant Mana Fixture",
            "Attached Grant Mill Equipment Fixture",
            "Attached Grant Draw Discard Equipment Fixture",
            "Attached Grant Return Aura Fixture",
        ):
            record = self.db.lookup(name)
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ):
                engine.semantics.put(program)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        return session

    def add_fixture(
        self,
        engine,
        *,
        name: str,
        ref: str,
        seat: str = "A",
    ) -> CardInstance:
        record = self.db.lookup(name)
        value = CardInstance(
            object_id=f"attached-grant:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
            entered_battlefield_turn_sequence=(
                engine.state.turn_sequence - 1
            ),
        )
        engine.state.cards[value.object_id] = value
        engine.state.players[seat].zones["battlefield"].append(value.object_id)
        return value

    @staticmethod
    def creature(engine, *, ref: str, seat: str = "A") -> CardInstance:
        created = engine.create_token(
            seat,
            name=f"Grant recipient {ref}",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
                "keywords": [],
            },
        )[0]
        value = engine._resolve_object(
            seat,
            created,
            zones={"battlefield"},
        )
        value.entered_battlefield_turn_sequence = (
            engine.state.turn_sequence - 1
        )
        value.acquired_control_turn_count = -1
        return value

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def equip(self, engine, equipment: CardInstance, recipient: CardInstance):
        ability = next(
            ability
            for ability in engine._activated_abilities(equipment)
            if ability.builtin_semantic_key == "builtin:equip"
        )
        engine.state.players[equipment.controller].mana_pool["C"] += 1
        engine.state.priority_player = equipment.controller
        engine._activate(
            equipment.controller,
            {
                "source": equipment.ref,
                "ability": ability.ability_id,
                "targets": [recipient.ref],
            },
        )
        self.resolve_top(engine)
        self.assertEqual(recipient.object_id, equipment.attached_to)

    def test_granted_activation_shares_offer_commit_lifetime_and_rollback(self):
        session = self.session(6131010)
        engine = session.engine
        aura = self.add_fixture(
            engine,
            name="Attached Grant Damage Fixture",
            ref="damage-aura",
        )
        recipient = self.creature(engine, ref="damage-recipient")
        attach_objects(
            engine.state.cards,
            aura,
            recipient,
            source_timestamp=engine._next_zone_timestamp(),
        )
        abilities = engine._activated_abilities(recipient)
        granted = next(
            ability
            for ability in abilities
            if ability.builtin_semantic_key
            == "fixture-attached-grant-damage:"
            "ability:granted:front:n2"
        )
        self.assertFalse(
            any(
                ability.builtin_semantic_key == granted.builtin_semantic_key
                for ability in engine._activated_abilities(aura)
            )
        )
        hints = engine._priority_action_hints("A")
        offers = hints["abilities"]
        self.assertTrue(
            any(
                offer["s"] == recipient.ref
                and offer["a"] == granted.ability_id
                for offer in offers
            ),
            hints,
        )

        stack_before = list(engine.state.stack)
        with self.assertRaises(GameRuleError):
            engine._activate(
                "A",
                {
                    "source": recipient.ref,
                    "ability": granted.ability_id,
                    "targets": ["missing-target"],
                },
            )
        self.assertFalse(recipient.tapped)
        self.assertEqual(stack_before, engine.state.stack)

        life_before = engine.state.players["B"].life
        engine._activate(
            "A",
            {
                "source": recipient.ref,
                "ability": granted.ability_id,
                "targets": ["B"],
            },
        )
        self.assertTrue(recipient.tapped)
        self.assertEqual(recipient.object_id, engine.state.stack[-1].source_object_id)
        self.assertEqual(granted.builtin_semantic_key, engine.state.stack[-1].semantic_key)
        self.resolve_top(engine)
        self.assertEqual(life_before - 1, engine.state.players["B"].life)

        detach_object(engine.state.cards, aura)
        self.assertFalse(
            any(
                ability.builtin_semantic_key == granted.builtin_semantic_key
                for ability in engine._activated_abilities(recipient)
            )
        )
        attach_objects(
            engine.state.cards,
            aura,
            recipient,
            source_timestamp=engine._next_zone_timestamp(),
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="test:remove-grant-source-abilities",
                source_id="test:ability-removal",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=aura.object_id,
                        logical_object_id=aura.logical_object_id,
                    ),
                ),
            ),
        )
        self.assertFalse(
            any(
                ability.builtin_semantic_key == granted.builtin_semantic_key
                for ability in engine._activated_abilities(recipient)
            )
        )

    def test_granted_mana_ability_is_recipient_bound_and_ends_on_departure(self):
        session = self.session(6131011)
        engine = session.engine
        aura = self.add_fixture(
            engine,
            name="Attached Grant Mana Fixture",
            ref="mana-aura",
        )
        recipient = self.creature(engine, ref="mana-recipient")
        attach_objects(
            engine.state.cards,
            aura,
            recipient,
            source_timestamp=engine._next_zone_timestamp(),
        )
        granted = next(
            ability
            for ability in engine._activated_abilities(recipient)
            if ability.mana_ability
            and ability.builtin_semantic_key
            == "fixture-attached-grant-mana:ability:granted:front:n2"
        )
        self.assertFalse(
            any(
                ability.builtin_semantic_key == granted.builtin_semantic_key
                for ability in engine._activated_abilities(aura)
            )
        )
        before = engine.state.players["A"].mana_pool["G"]
        engine._activate(
            "A",
            {
                "source": recipient.ref,
                "ability": granted.ability_id,
            },
        )
        self.assertTrue(recipient.tapped)
        self.assertEqual(before + 1, engine.state.players["A"].mana_pool["G"])

        engine.move_card(aura.object_id, "graveyard", log=False)
        self.assertFalse(
            any(
                ability.builtin_semantic_key == granted.builtin_semantic_key
                for ability in engine._activated_abilities(recipient)
            )
        )

    def test_granted_triggers_preserve_multiplicity_and_replay(self):
        session = self.session(6131012)
        engine = session.engine
        recipient = self.creature(engine, ref="trigger-recipient")
        for index in range(2):
            equipment = self.add_fixture(
                engine,
                name="Attached Grant Trigger Fixture",
                ref=f"trigger-equipment-{index}",
            )
            attach_objects(
                engine.state.cards,
                equipment,
                recipient,
                source_timestamp=engine._next_zone_timestamp(),
            )
        program = engine.semantics.get(
            "fixture-attached-grant-trigger:trigger:front:n1:granted"
        )
        self.assertIsNotNone(program)
        self.assertTrue(program.provenance["granted_only"])

        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        life_before = engine.state.players["A"].life
        declared = session.act(
            "pilot:A",
            {"a": "attack", "atk": {recipient.ref: "B"}},
        )
        self.assertTrue(declared.ok, declared.summary)
        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        pending = [
            item
            for batch in engine.state.pending_trigger_batches
            for item in batch.items
            if item.source_ability_id == program.key
        ]
        self.assertEqual(2, len(pending))
        self.assertTrue(
            all(item.source_object_id == recipient.object_id for item in pending)
        )
        ordered = session.act(
            "pilot:A",
            {
                "action_id": "order",
                "triggers": [item.ref for item in pending],
            },
        )
        self.assertTrue(ordered.ok, ordered.summary)
        triggered = [
            item
            for item in engine.state.stack
            if item.semantic_key == program.key
        ]
        self.assertEqual(2, len(triggered))
        for _ in range(16):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(life_before + 4, engine.state.players["A"].life)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "attached-grant-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_equipped_granted_mill_uses_activation_and_zone_owners(self):
        session = self.session(6131013)
        engine = session.engine
        equipment = self.add_fixture(
            engine,
            name="Attached Grant Mill Equipment Fixture",
            ref="mill-equipment",
        )
        recipient = self.creature(engine, ref="mill-recipient")
        self.equip(engine, equipment, recipient)
        granted = next(
            ability
            for ability in engine._activated_abilities(recipient)
            if ability.builtin_semantic_key
            == "fixture-attached-grant-mill-equipment:"
            "ability:granted:front:n1"
        )
        engine.state.players["A"].mana_pool["C"] = 2
        engine.state.priority_player = "A"
        library_before = len(engine.state.players["B"].zones["library"])
        graveyard_before = len(engine.state.players["B"].zones["graveyard"])
        engine._activate(
            "A",
            {
                "source": recipient.ref,
                "ability": granted.ability_id,
                "targets": ["B"],
            },
        )
        self.resolve_top(engine)
        self.assertEqual(
            library_before - 3,
            len(engine.state.players["B"].zones["library"]),
        )
        self.assertEqual(
            graveyard_before + 3,
            len(engine.state.players["B"].zones["graveyard"]),
        )
        self.assertEqual(recipient.object_id, equipment.attached_to)

    def test_equipped_granted_discard_obeys_destination_replacement(self):
        session = self.session(6131014)
        engine = session.engine
        equipment = self.add_fixture(
            engine,
            name="Attached Grant Draw Discard Equipment Fixture",
            ref="discard-equipment",
        )
        recipient = self.creature(engine, ref="discard-recipient")
        self.equip(engine, equipment, recipient)
        replacement = self.add_fixture(
            engine,
            name="Dauthi Voidwalker",
            ref="discard-replacement",
            seat="B",
        )
        self.assertTrue(
            engine.semantics.programs_for_oracle(replacement.oracle_id)
        )
        chosen = engine.state.cards[
            engine.state.players["A"].zones["hand"][0]
        ]
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        engine._issue_attackers()
        attacked = session.act(
            "pilot:A",
            {"a": "attack", "atk": {recipient.ref: "B"}},
        )
        self.assertTrue(attacked.ok, attacked.summary)
        self.assertTrue(
            any(
                item.semantic_key
                == "fixture-attached-grant-draw-discard-equipment:"
                "trigger:front:n1:granted"
                for item in engine.state.stack
            )
        )
        self.resolve_top(engine)
        self.assertEqual("choice.apnap", engine.state.pending_decision.kind)
        discarded = session.act(
            "pilot:A",
            {"action_id": "choose", "cards": [chosen.ref]},
        )
        self.assertTrue(discarded.ok, discarded.summary)
        while (
            engine.state.pending_decision is not None
            and engine.state.pending_decision.kind == "replacement.order"
        ):
            decision = session.packet("pilot:A", full=True)["decision"]
            selected = decision["ctx"]["options"][0]["id"]
            ordered = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "replacement": selected,
                    "plan": "ORDER_REPLACEMENTS",
                },
            )
            self.assertTrue(ordered.ok, ordered.summary)
        self.assertEqual("exile", chosen.zone)
        self.assertEqual(1, chosen.counters["void"])
        self.assertEqual(recipient.object_id, equipment.attached_to)

    def test_granted_target_activation_survives_granting_aura_self_return(self):
        session = self.session(6131015)
        engine = session.engine
        aura = self.add_fixture(
            engine,
            name="Attached Grant Return Aura Fixture",
            ref="return-aura",
        )
        recipient = self.creature(engine, ref="return-recipient")
        attach_objects(
            engine.state.cards,
            aura,
            recipient,
            source_timestamp=engine._next_zone_timestamp(),
        )
        granted = next(
            ability
            for ability in engine._activated_abilities(recipient)
            if ability.builtin_semantic_key
            == "fixture-attached-grant-return-aura:"
            "ability:granted:front:n2"
        )
        return_ability = next(
            ability
            for ability in engine._activated_abilities(aura)
            if ability.builtin_semantic_key
            == "fixture-attached-grant-return-aura:ability:ab3"
        )
        life_before = engine.state.players["B"].life
        engine._activate(
            "A",
            {
                "source": recipient.ref,
                "ability": granted.ability_id,
                "targets": ["B"],
            },
        )
        engine.state.players["A"].mana_pool["C"] = 1
        engine.state.players["A"].mana_pool["U"] = 1
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": aura.ref,
                "ability": return_ability.ability_id,
            },
        )
        self.resolve_top(engine)
        self.assertEqual("hand", aura.zone)
        self.assertFalse(
            any(
                ability.builtin_semantic_key == granted.builtin_semantic_key
                for ability in engine._activated_abilities(recipient)
            )
        )
        self.resolve_top(engine)
        self.assertEqual(life_before - 1, engine.state.players["B"].life)


if __name__ == "__main__":
    unittest.main()
