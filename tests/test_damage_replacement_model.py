from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import unittest

from quorune import replacement_effects

from common import ROOT, keep_all, make_session
from property_budget import property_transitions
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase
from quorune.damage import (
    commit_prepared_damage_batch,
    damage_proposal,
    DamageEvent,
    DamageError,
    DamageRecipientSnapshot,
    prepare_damage_batch,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckLoader
from quorune.engine import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.object_predicate import ObjectQuerySpec
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.compiler.fixed_all_damage_prevention import (
    fixed_all_damage_prevention_specs,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.replacement_effects import (
    ReplacementChoiceRequired,
    resolve_replacements,
)
from quorune.semantic_runtime import (
    AllDamagePreventionHandler,
    DamageQuantityReplacementHandler,
    DamageQuantityReplacementV2Handler,
    DamageReplacementSourceContext,
    FixedDamagePreventionHandler,
    StaticDamageRedirectionHandler,
    SemanticNodeError,
    default_damage_replacement_registry,
)
from quorune.semantics import SemanticProgram


from damage_replacement_support import (
    additive_quantity_descriptor,
    DamageReplacementPipelineBase,
    damage_condition,
    prevention_descriptor,
    quantity_descriptor,
)


class DamageReplacementModelTests(DamageReplacementPipelineBase):
    """Focused CR 120/614/615/616 damage transaction witnesses."""

    def test_runtime_components_validate_exact_bounded_shapes(self):
        quantity = DamageQuantityReplacementHandler()
        prevention = FixedDamagePreventionHandler()
        context = DamageReplacementSourceContext(
            source_ref="replacement-source",
            source_controller="A",
        )

        doubled = quantity.replacement_effect(quantity_descriptor(), context)
        self.assertEqual("damage", doubled.event_kind)
        self.assertEqual(
            ({"op": "multiply", "field": "amount", "factor": 2},),
            tuple(operation.to_dict() for operation in doubled.operations),
        )
        fixed = prevention.replacement_effect(
            prevention_descriptor(amount=2), context
        )
        self.assertEqual(
            ({"op": "prevent", "amount": 2},),
            tuple(operation.to_dict() for operation in fixed.operations),
        )

        redirect = StaticDamageRedirectionHandler()
        destination = replacement_effects.RedirectDamage(
            target="C1",
            target_kind="permanent",
            target_controller="B",
            target_object_id="source-object",
            target_logical_object_id="source-incarnation",
            target_owner="B",
            target_types=("creature",),
        )
        redirected = redirect.replacement_effect(
            {
                "handler_id": redirect.handler_id,
                "schema_version": 1,
                "event": "damage",
                "condition": damage_condition(
                    target_controller_relation="source_controller",
                    target_kinds=["player"],
                ),
                "modification": {"destination": "source"},
            },
            DamageReplacementSourceContext(
                source_ref="C1",
                source_controller="B",
                source_destination=destination,
            ),
        )
        self.assertEqual(
            destination.to_dict(), redirected.operations[0].to_dict()
        )

        with self.assertRaisesRegex(SemanticNodeError, "positive integer"):
            quantity.validate(quantity_descriptor(multiplier=0))
        with self.assertRaisesRegex(SemanticNodeError, "positive integer"):
            prevention.validate(prevention_descriptor(amount=0))
        malformed = quantity_descriptor()
        malformed["condition"]["combat"] = "sometimes"
        with self.assertRaisesRegex(SemanticNodeError, "boolean or null"):
            quantity.validate(malformed)
        malformed = prevention_descriptor()
        malformed["condition"]["unknown"] = True
        with self.assertRaisesRegex(SemanticNodeError, "unknown fields"):
            prevention.validate(malformed)

        inventory = default_damage_replacement_registry().inventory()
        self.assertEqual(
            [
                "prevention.damage.all.v1",
                "prevention.damage.fixed.v1",
                "replacement.damage.quantity.v1",
                "replacement.damage.quantity.v2",
                "replacement.damage.redirect-to-source.v1",
            ],
            sorted(item["handler_id"] for item in inventory),
        )

    def test_fixed_all_prevention_compiles_across_existing_typed_owners(self):
        capabilities = load_default_capability_registry()
        expectations = {
            "Scoped Prevention Ward": (
                "prevention_effect",
                "prevention.damage.all.v1",
            ),
            "Attached Prevention Mantle": (
                "prevention_effect",
                "prevention.damage.all.v1",
            ),
            "Turnbound Prevention Adept": (
                "prevention_effect",
                "prevention.damage.all.v1",
            ),
            "Scoped Prevention Haze": (
                "spell_ability",
                "create_damage_prevention_shield",
            ),
            "Targeted Prevention Technique": (
                "spell_ability",
                "create_damage_prevention_shield",
            ),
            "Entry Prevention Guardian": (
                "triggered_ability",
                "create_damage_prevention_shield",
            ),
        }
        for card_name, (kind, operation) in expectations.items():
            with self.subTest(card_name=card_name):
                ir = compile_oracle_card(
                    self.db.lookup(card_name),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.to_dict())
                node = next(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.kind == kind
                )
                if operation.startswith("prevention."):
                    self.assertEqual(operation, node.handlers[0]["handler_id"])
                else:
                    self.assertTrue(node.effects)
                    self.assertTrue(
                        all(effect["op"] == operation for effect in node.effects)
                    )
                    self.assertTrue(
                        all("scope" in effect for effect in node.effects)
                    )
        targeted = compile_oracle_card(
            self.db.lookup("Targeted Prevention Technique"),
            capability_registry=capabilities,
            capability_profile="commander_review",
        ).faces[0].nodes[0]
        self.assertEqual("attacking", targeted.target_schema["combat_state"])

        for unsupported in (
            "Prevent the next 3 damage that would be dealt to you this turn.",
            "Prevent all but 1 of that damage.",
            "You may prevent all damage that would be dealt to you this turn.",
            "Prevent all damage a source of your choice would deal this turn.",
            "Prevent all damage creatures with power 4 or less would deal this turn.",
            "Prevent all damage that would be dealt to a creature by another "
            "creature if they share a color.",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertIsNone(
                    fixed_all_damage_prevention_specs(
                        unsupported,
                        card_name="Generic Prevention Fixture",
                    )
                )

    def test_all_prevention_handler_fails_closed_and_lowers_negative_scope(self):
        handler = AllDamagePreventionHandler()
        scope = {
            "source_controller_relation": "opponent",
            "target_controller_relation": "source_controller",
            "target_kinds": ["permanent"],
            "source_characteristics_all": ["creature"],
            "source_characteristics_any": [],
            "source_characteristics_none": ["human"],
            "source_colors_any": [],
            "source_colors_none": ["G"],
            "target_characteristics_all": ["creature"],
            "target_characteristics_any": [],
            "target_characteristics_none": [],
            "source_ref": None,
            "target_ref": None,
            "excluded_source_ref": None,
            "excluded_target_ref": "fixture-source",
        }
        descriptor = {
            "handler_id": handler.handler_id,
            "schema_version": 1,
            "event": "damage",
            "condition": {
                "scopes": [
                    {
                        "damage_kind": "noncombat",
                        "source_controller_turn_only": False,
                        "scope": scope,
                    }
                ]
            },
            "modification": {"amount": "all"},
        }
        effects = handler.lower(
            descriptor,
            DamageReplacementSourceContext(
                source_ref="fixture-source",
                source_controller="A",
                active_player="B",
            ),
        )
        self.assertEqual(1, len(effects))
        self.assertEqual(
            ("human",),
            effects[0].conditions["source_characteristics"]["contains_none"],
        )
        self.assertEqual(
            ("G",), effects[0].conditions["source_colors"]["contains_none"]
        )
        self.assertEqual(
            ({"op": "prevent"},),
            tuple(operation.to_dict() for operation in effects[0].operations),
        )
        quantity = DamageQuantityReplacementHandler().replacement_effect(
            quantity_descriptor(multiplier=2),
            DamageReplacementSourceContext(
                source_ref="quantity-source",
                source_controller="A",
            ),
        )
        event = replacement_effects.ReplaceableEvent(
            event_id="damage:scoped-ordering",
            kind="damage",
            affected_player="A",
            payload={
                "amount": 3,
                "prevented": 0,
                "source": "opponent-creature",
                "source_controller": "B",
                "source_characteristics": ["creature"],
                "source_colors": ["R"],
                "target": "protected-creature",
                "target_controller": "A",
                "target_kind": "permanent",
                "target_characteristics": ["creature"],
                "combat": False,
                "unpreventable": False,
            },
        )
        prevent_first = resolve_replacements(
            event,
            (quantity, effects[0]),
            selections=(effects[0].effect_id,),
        )
        quantity_first = resolve_replacements(
            event,
            (quantity, effects[0]),
            selections=(quantity.effect_id, effects[0].effect_id),
        )
        self.assertEqual(
            (0, 3),
            (
                prevent_first.payload["amount"],
                prevent_first.payload["prevented"],
            ),
        )
        self.assertEqual(
            (0, 6),
            (
                quantity_first.payload["amount"],
                quantity_first.payload["prevented"],
            ),
        )
        malformed = {
            **descriptor,
            "condition": {
                "scopes": [
                    {
                        **descriptor["condition"]["scopes"][0],
                        "damage_kind": "sometimes",
                    }
                ]
            },
        }
        with self.assertRaisesRegex(SemanticNodeError, "kind is unsupported"):
            handler.validate(malformed)

    def test_additive_damage_v2_matches_color_or_type_and_excludes_self(self):
        handler = DamageQuantityReplacementV2Handler()
        descriptor = additive_quantity_descriptor(
            additional=1,
            target_controller_relation="any",
            source_colors_all=["R"],
            exclude_source_ref=True,
        )
        effect = handler.replacement_effect(
            descriptor,
            DamageReplacementSourceContext(
                source_ref="jaya",
                source_controller="A",
            ),
        )
        self.assertEqual(
            ({"op": "add", "field": "amount", "amount": 1},),
            tuple(operation.to_dict() for operation in effect.operations),
        )
        self.assertEqual(
            ("R",), effect.conditions["source_colors"]["contains_all"]
        )
        self.assertEqual(("jaya",), effect.conditions["source"]["not_in"])

        other = replacement_effects.ReplaceableEvent(
            event_id="damage:other-red",
            kind="damage",
            affected_player="B",
            payload={
                "source": "chandra",
                "source_controller": "A",
                "source_colors": ["R"],
                "source_characteristics": ["planeswalker"],
                "target_controller": "B",
                "target_kind": "player",
                "target_characteristics": [],
                "combat": False,
                "amount": 2,
            },
        )
        own = replacement_effects.ReplaceableEvent(
            event_id="damage:jaya",
            kind="damage",
            affected_player="B",
            payload={**dict(other.payload), "source": "jaya"},
        )
        nonred = replacement_effects.ReplaceableEvent(
            event_id="damage:blue",
            kind="damage",
            affected_player="B",
            payload={
                **dict(other.payload),
                "source": "jace",
                "source_colors": ["U"],
            },
        )
        self.assertEqual(
            3,
            resolve_replacements(
                other, (effect,), selections=(effect.effect_id,)
            ).payload["amount"],
        )
        self.assertEqual(
            2,
            resolve_replacements(own, (effect,), selections=()).payload[
                "amount"
            ],
        )
        self.assertEqual(
            2,
            resolve_replacements(nonred, (effect,), selections=()).payload[
                "amount"
            ],
        )

        malformed = additive_quantity_descriptor()
        malformed["condition"]["exclude_source_ref"] = "sometimes"
        with self.assertRaisesRegex(SemanticNodeError, "must be a boolean"):
            handler.validate(malformed)
        competing = additive_quantity_descriptor(
            source_types_all=["instant"],
            source_types_any=["sorcery"],
        )
        with self.assertRaisesRegex(SemanticNodeError, "all and any"):
            handler.validate(competing)


    def test_damage_value_objects_reject_unknown_recipient_kinds(self):
        with self.assertRaisesRegex(DamageError, "player or permanent"):
            DamageRecipientSnapshot(  # type: ignore[arg-type]
                ref="B",
                kind="battlefield",
                controller="B",
            )
        with self.assertRaisesRegex(ValueError, "player or permanent"):
            DamageEvent(  # type: ignore[arg-type]
                source="source",
                source_object_id="source-object",
                source_logical_object_id="source-incarnation",
                source_oracle_id=None,
                source_commander_designation_id=None,
                source_controller="A",
                source_owner="A",
                source_types=(),
                source_subtypes=(),
                source_colors=(),
                source_keywords=(),
                source_is_commander=False,
                target="B",
                target_kind="battlefield",
                target_object_id=None,
                target_controller="B",
                target_types=(),
                target_subtypes=(),
                assigned_amount=1,
                dealt_amount=1,
                prevented_amount=0,
                combat=False,
            )


    def test_prevention_to_zero_ends_the_damage_replacement_event(self):
        context = DamageReplacementSourceContext(
            source_ref="replacement-source",
            source_controller="A",
        )
        multiply = DamageQuantityReplacementHandler().replacement_effect(
            quantity_descriptor(multiplier=2), context
        )
        prevent = FixedDamagePreventionHandler().replacement_effect(
            prevention_descriptor(amount=1), context
        )

        resolved = resolve_replacements(
            self._property_event(1, 1208001),
            (multiply, prevent),
            selections=(prevent.effect_id,),
        )

        self.assertEqual(0, resolved.payload["amount"])
        self.assertEqual(1, resolved.payload["prevented"])
        self.assertEqual((prevent.effect_id,), resolved.applied_effects)


    def test_furnace_and_daunting_order_changes_final_damage(self):
        session = self.session(120461501)
        engine = session.engine
        _furnace, defender, source = self.stage_sources(engine)
        proposal = self.proposal(engine, source=source, target=defender)

        with self.assertRaises(ReplacementChoiceRequired) as required:
            prepare_damage_batch(engine, (proposal,))
        self.assertEqual("B", required.exception.pending.choice.chooser)
        options = required.exception.pending.choice.options
        furnace = next(value for value in options if "quantity" in value)
        prevention = next(value for value in options if "fixed" in value)

        prepared = prepare_damage_batch(
            engine,
            (proposal,),
            selections=(prevention,),
        )
        result = commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(4, result.events[0].dealt_amount)
        self.assertEqual(1, result.events[0].prevented_amount)
        self.assertEqual(3, result.events[0].assigned_amount)
        self.assertEqual(4, defender.marked_damage)

        defender.marked_damage = 0
        prepared = prepare_damage_batch(
            engine,
            (proposal,),
            selections=(furnace,),
        )
        result = commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(5, result.events[0].dealt_amount)
        self.assertEqual(1, result.events[0].prevented_amount)
        self.assertEqual(5, defender.marked_damage)


    def test_static_prevention_applies_to_each_simultaneous_event(self):
        session = self.session(120461502)
        engine = session.engine
        defender = self.add_permanent(
            engine,
            seat="B",
            name="Daunting Defender",
            ref="b-defender",
        )
        defender.counters["+1/+1"] = 5
        source = self.add_permanent(
            engine,
            seat="A",
            name="Mishra, Eminent One",
            ref="a-source",
        )
        proposals = (
            self.proposal(
                engine,
                source=source,
                target=defender,
                event_id="damage:one",
            ),
            self.proposal(
                engine,
                source=source,
                target=defender,
                event_id="damage:two",
            ),
        )

        prepared = prepare_damage_batch(engine, proposals)
        result = commit_prepared_damage_batch(engine, prepared)
        self.assertEqual([2, 2], [event.dealt_amount for event in result.events])
        self.assertEqual([1, 1], [event.prevented_amount for event in result.events])
        self.assertEqual(4, defender.marked_damage)

    def test_static_all_prevention_tracks_controller_ability_and_source_lifecycle(self):
        engine = self.session(120461504).engine

        def resolved(proposal):
            try:
                prepared = prepare_damage_batch(engine, (proposal,))
            except ReplacementChoiceRequired as required:
                effect_id = next(
                    value
                    for value in required.pending.choice.options
                    if "prevention.damage.all" in value
                )
                prepared = prepare_damage_batch(
                    engine,
                    (proposal,),
                    selections=(effect_id,),
                )
            return commit_prepared_damage_batch(engine, prepared).events[0]

        ward = self.add_permanent(
            engine,
            seat="A",
            name="Scoped Prevention Ward",
            ref="a-scoped-ward",
        )
        protected = self.add_permanent(
            engine,
            seat="A",
            name="Entry Prevention Guardian",
            ref="a-protected",
        )
        opponent_permanent = self.add_permanent(
            engine,
            seat="B",
            name="Entry Prevention Guardian",
            ref="b-protected",
        )
        damage_source = self.add_permanent(
            engine,
            seat="B",
            name="Mishra, Eminent One",
            ref="b-damage-source",
        )
        protected.counters["+1/+1"] = 10
        opponent_permanent.counters["+1/+1"] = 10

        prevented = resolved(
            self.proposal(engine, source=damage_source, target=protected)
        )
        self.assertEqual((0, 3), (prevented.dealt_amount, prevented.prevented_amount))
        combat = resolved(
            self.proposal(
                engine,
                source=damage_source,
                target=protected,
                event_id="damage:combat-not-matched",
                combat=True,
            )
        )
        self.assertEqual((3, 0), (combat.dealt_amount, combat.prevented_amount))
        unprotected = resolved(
            self.proposal(
                engine,
                source=damage_source,
                target=opponent_permanent,
                event_id="damage:wrong-controller",
            )
        )
        self.assertEqual(
            (3, 0),
            (unprotected.dealt_amount, unprotected.prevented_amount),
        )

        ward.controller = "B"
        changed_controller = resolved(
            self.proposal(
                engine,
                source=damage_source,
                target=opponent_permanent,
                event_id="damage:changed-controller",
            )
        )
        self.assertEqual(
            (0, 3),
            (changed_controller.dealt_amount, changed_controller.prevented_amount),
        )

        identity = ContinuousObjectIdentity(
            object_id=ward.object_id,
            logical_object_id=ward.logical_object_id,
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-prevention-ability",
                source_id="fixture:remove-abilities-source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(identity,),
            ),
        )
        removed = resolved(
            self.proposal(
                engine,
                source=damage_source,
                target=opponent_permanent,
                event_id="damage:ability-removed",
            )
        )
        self.assertEqual((3, 0), (removed.dealt_amount, removed.prevented_amount))
        engine.state.continuous_effects.clear()
        engine.state.players["A"].zones["battlefield"].remove(ward.object_id)
        ward.zone = "graveyard"
        departed = resolved(
            self.proposal(
                engine,
                source=damage_source,
                target=opponent_permanent,
                event_id="damage:source-departed",
            )
        )
        self.assertEqual((3, 0), (departed.dealt_amount, departed.prevented_amount))

        turnbound = self.add_permanent(
            engine,
            seat="A",
            name="Turnbound Prevention Adept",
            ref="a-turnbound-prevention",
        )
        engine.state.active_player = "A"
        own_turn = resolved(
            self.proposal(
                engine,
                source=damage_source,
                target=turnbound,
                event_id="damage:source-controller-turn",
            )
        )
        self.assertEqual((0, 3), (own_turn.dealt_amount, own_turn.prevented_amount))
        engine.state.active_player = "B"
        other_turn = resolved(
            self.proposal(
                engine,
                source=damage_source,
                target=turnbound,
                event_id="damage:not-source-controller-turn",
            )
        )
        self.assertEqual(
            (3, 0), (other_turn.dealt_amount, other_turn.prevented_amount)
        )

    def test_attached_static_prevention_uses_current_attachment_and_source_facts(self):
        engine = self.session(120461505).engine

        def resolved(proposal):
            try:
                prepared = prepare_damage_batch(engine, (proposal,))
            except ReplacementChoiceRequired as required:
                effect_id = next(
                    value
                    for value in required.pending.choice.options
                    if "prevention.damage.all" in value
                )
                prepared = prepare_damage_batch(
                    engine,
                    (proposal,),
                    selections=(effect_id,),
                )
            return commit_prepared_damage_batch(engine, prepared).events[0]

        protected = self.add_permanent(
            engine,
            seat="A",
            name="Entry Prevention Guardian",
            ref="a-attached-protected",
        )
        protected.counters["+1/+1"] = 10
        mantle = self.add_permanent(
            engine,
            seat="A",
            name="Attached Prevention Mantle",
            ref="a-prevention-mantle",
        )
        mantle.attached_to = protected.object_id
        artifact_source = self.add_permanent(
            engine,
            seat="B",
            name="Entry Prevention Guardian",
            ref="b-artifact-source",
        )
        ordinary_source = self.add_permanent(
            engine,
            seat="B",
            name="White Knight",
            ref="b-creature-source",
        )

        artifact = resolved(
            self.proposal(engine, source=artifact_source, target=protected)
        )
        self.assertEqual((0, 3), (artifact.dealt_amount, artifact.prevented_amount))
        ordinary = resolved(
            self.proposal(
                engine,
                source=ordinary_source,
                target=protected,
                event_id="damage:nonartifact-source",
            )
        )
        self.assertEqual((3, 0), (ordinary.dealt_amount, ordinary.prevented_amount))
        mantle.attached_to = None
        detached = resolved(
            self.proposal(
                engine,
                source=artifact_source,
                target=protected,
                event_id="damage:detached",
            )
        )
        self.assertEqual((3, 0), (detached.dealt_amount, detached.prevented_amount))


    def test_unpreventable_damage_applies_prevention_without_reducing_damage(self):
        session = self.session(120461503)
        engine = session.engine
        defender = self.add_permanent(
            engine,
            seat="B",
            name="Daunting Defender",
            ref="b-defender",
        )
        source = self.add_permanent(
            engine,
            seat="A",
            name="Mishra, Eminent One",
            ref="a-source",
        )
        prepared = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=source,
                    target=defender,
                    unpreventable=True,
                ),
            ),
        )
        result = commit_prepared_damage_batch(engine, prepared)
        event = result.events[0]
        self.assertEqual(3, event.dealt_amount)
        self.assertEqual(0, event.prevented_amount)
        self.assertTrue(event.unpreventable)
        self.assertEqual(1, len(event.applied_effects))


    def test_damage_amount_pipeline_property_1000_deterministic_transitions(self):
        quantity = DamageQuantityReplacementHandler()
        prevention = FixedDamagePreventionHandler()
        randomizer = random.Random(120461599)
        for index in range(property_transitions()):
            amount = randomizer.randint(1, 20)
            multiplier = randomizer.randint(2, 4)
            prevented = randomizer.randint(1, 8)
            context = DamageReplacementSourceContext(
                source_ref=f"source-{index}",
                source_controller="A",
            )
            multiply = quantity.replacement_effect(
                quantity_descriptor(multiplier=multiplier), context
            )
            prevent = prevention.replacement_effect(
                prevention_descriptor(amount=prevented), context
            )
            event = self._property_event(amount, index)
            first = resolve_replacements(
                event,
                (multiply, prevent),
                selections=(multiply.effect_id, prevent.effect_id),
            )
            second_selections = (
                (prevent.effect_id, multiply.effect_id)
                if prevented < amount
                else (prevent.effect_id,)
            )
            second = resolve_replacements(
                event,
                (multiply, prevent),
                selections=second_selections,
            )
            self.assertEqual(
                max(0, amount * multiplier - prevented),
                first.payload["amount"],
            )
            self.assertEqual(
                max(0, amount - prevented) * multiplier,
                second.payload["amount"],
            )



if __name__ == "__main__":
    unittest.main()
