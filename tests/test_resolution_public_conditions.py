from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
import tempfile
from dataclasses import replace
from unittest.mock import patch

from quorune.compiler.resolution_condition_templates import resolution_condition_template
from quorune.oracle_ir import _reviewed_effect_template
from quorune.resolution_conditions import RESOLUTION_CONDITION_CAPABILITY
from quorune.rules.capabilities import capability_dependencies_for_node
from quorune.rules.capabilities import load_default_capability_registry
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from common import ROOT


class ResolutionPublicConditionCompilerTests(unittest.TestCase):
    def test_bare_subtype_condition_retains_permanent_not_creature_domain(self):
        # CR 109.2: a bare subtype denotes battlefield permanents, including Kindred.
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        bare = fixed_public_state_condition("you control a Faerie", source_name="Generic Condition")
        creature = fixed_public_state_condition("you control a Faerie creature", source_name="Generic Condition")
        self.assertEqual(("faerie",), bare.quantity.query.subtypes_all)
        self.assertEqual((), bare.quantity.query.types_all)
        self.assertEqual(("creature",), creature.quantity.query.types_all)

    def test_derived_conditional_results_remain_residual_without_timing_proof(self):
        examples = (
            "If you control an artifact, you gain life equal to this creature's power.",
            "If you control an artifact, draw a card for each creature you control.",
            "If you control an artifact, this creature gets +1/+1 until end of turn.",
        )
        for text in examples:
            with self.subTest(text=text):
                compiled = _reviewed_effect_template(text, card_name="Generic Condition", source_is_permanent=True)
                self.assertIsNone(compiled[0], "Derived conditional results are outside the fixed-result boundary")
        # The existing unconditional quantity owners remain available.
        for body in ("You gain life equal to this creature's power.", "Draw a card for each creature you control."):
            self.assertIsNotNone(_reviewed_effect_template(body, card_name="Generic Condition", source_is_permanent=True)[0])

    def test_condition_uses_real_spell_trigger_and_activation_shells(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card

        registry = load_default_capability_registry()
        for text, type_line, expected_kind in (
            ("Draw a card. If you control an artifact, you gain 3 life.", "Instant", "spell_ability"),
            ("When this creature enters, draw a card. If you control an artifact, you gain 3 life.", "Creature — Wizard", "triggered_ability"),
            ("{T}: Draw a card. If you control an artifact, you gain 3 life.", "Creature — Wizard", "activated_ability"),
            ("{1}, Sacrifice this artifact: Draw a card. If you control an artifact, you gain 3 life.", "Artifact", "activated_ability"),
        ):
            with self.subTest(text=text):
                compiled = compile_oracle_card(record(text, type_line), capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", compiled.status, compiled.material_residuals)
                node = next(node for face in compiled.faces for node in face.nodes if node.kind == expected_kind)
                self.assertIn(RESOLUTION_CONDITION_CAPABILITY, node.capability_dependencies)

    def test_condition_keeps_printed_order_and_child_capability_shapes(self):
        examples = (
            ("Destroy target creature. If a creature died this turn, draw a card.", "destroy", "draw"),
            ("Draw a card. If you have 5 or less life, you gain 3 life.", "draw", "life"),
            ("Create a Treasure token. If you control an artifact, draw a card.", "create_token", "draw"),
            ("Draw a card. If you control a blue creature, draw a card, then discard a card.", "draw", "draw"),
        )
        for text, prefix_op, child_op in examples:
            with self.subTest(text=text):
                template, effects, schema, mechanics = _reviewed_effect_template(text, card_name="Generic Condition")
                self.assertEqual(template, "fixed-resolution-public-condition-v1")
                self.assertEqual(effects[0]["op"], prefix_op)
                self.assertEqual(effects[-1]["op"], "apply_if_public_condition")
                self.assertEqual(effects[-1]["effects"][0]["op"], child_op)
                dependencies = capability_dependencies_for_node(effects=effects, target_schema=schema, mechanic_ids=mechanics)
                self.assertIn(RESOLUTION_CONDITION_CAPABILITY, dependencies)
                malformed = copy.deepcopy(effects)
                malformed[-1]["condition"]["amount"] = True
                self.assertEqual(capability_dependencies_for_node(effects=malformed, target_schema=schema, mechanic_ids=mechanics), ())
                wrong = copy.deepcopy(effects)
                wrong[-1]["effects"][0]["op"] = "unrepresented_operation"
                self.assertEqual(capability_dependencies_for_node(effects=wrong, target_schema=schema, mechanic_ids=mechanics), ())

    def test_conditional_exclusions_are_fully_consumed(self):
        for text in (
            "Draw a card. If you control an artifact, draw two cards instead.",
            "Draw a card. If you control an artifact, destroy target creature.",
            "Draw a card. If this creature has flying, draw a card.",
            "Draw a card. If you control another creature, draw a card.",
            "Draw a card. If you control an artifact, draw a card. Otherwise, gain 3 life.",
            "Draw a card. If you control an artifact, if you control a creature, draw a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(resolution_condition_template(
                    text, source_name="Generic Condition",
                    compile_component=lambda body: _reviewed_effect_template(body, card_name="Generic Condition"),
                ))

    def test_handler_binds_controller_and_rejects_unavailable_facts(self):
        from quorune.semantic_choices.resolution_condition import PublicResolutionConditionHandler
        from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
        from quorune.util import stable_json
        from quorune.semantic_choices.model import SemanticChoiceError

        effect = _reviewed_effect_template("If you have 5 or less life, draw a card.", card_name="Generic Condition")[1][0]
        effect = {**effect, "player": "A"}
        effect["effects"][0]["player"] = "A"
        context = SemanticChoiceContext(
            actor="A", stack_ref="stack:condition", stack_controller="A", stack_label="Condition",
            source_ref="old-source", card_ref=None, semantic_program_id="generic-condition", semantic_program_version=1,
            query=SnapshotSemanticChoiceQuery(seat_order=("A", "B", "C", "D"), active_order=("A", "B", "C", "D")),
        )
        handler = PublicResolutionConditionHandler()
        with self.assertRaisesRegex(ValueError, "not materialized"):
            handler.prepare(effect, context)
        for matches in (False, True):
            query = replace(context.query, resolution_condition_facts={stable_json(effect["condition"]): matches})
            preparation = handler.prepare(effect, replace(context, query=query))
            self.assertIsNone(preparation.request)
            self.assertEqual(int(matches), len(preparation.auto_continue.prepend_effects))
        with self.assertRaisesRegex(SemanticChoiceError, "resolving controller"):
            handler.prepare({**effect, "player": "B"}, context)

    def test_canonical_query_distinguishes_unavailable_history_from_false(self):
        from types import SimpleNamespace
        from quorune.card_programs.runtime import fixed_public_state_condition_matches
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        from quorune.resolution_conditions import ResolutionConditionBinding
        from quorune.continuous_conditions import FixedPublicStateConditionError

        state = SimpleNamespace(players={"A": SimpleNamespace(life=40, in_game=True, zones={"hand": [], "graveyard": []})},
            cards={}, active_player="A", turn_sequence=1, turn_order=("A",), turn_history=None)
        condition = fixed_public_state_condition("a creature died this turn", source_name="Generic Condition")
        binding = ResolutionConditionBinding(controller="A")
        self.assertFalse(fixed_public_state_condition_matches(state, binding, condition,
            public_object_resolver=None, quantity_resolver=None))
        with self.assertRaisesRegex(FixedPublicStateConditionError, "unavailable"):
            fixed_public_state_condition_matches(state, binding, condition,
                public_object_resolver=None, quantity_resolver=None, require_available=True)


class ResolutionPublicConditionRuntimeTests(unittest.TestCase):
    # Reuse the real trusted registration, command, checkpoint and replay harness.
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _ActionHarness
    session = _ActionHarness.session
    add = _ActionHarness.add
    ready = _ActionHarness.ready
    checkpoint = _ActionHarness.checkpoint
    resolve = _ActionHarness.resolve
    replay = _ActionHarness.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "resolution-conditions.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/resolution-public-condition-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Generic Condition Deck", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 99),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_trusted_prefix_changes_condition_before_private_result_and_replay(self):
        session = self.session(4821001)
        engine = session.engine
        source = self.add(engine, "Generic Condition Treasure Draw", zone="hand")
        action = self.ready(session, source, {"G": 1})
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.checkpoint(session)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        hand_before = set(engine.state.players["A"].zones["hand"])
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(tokens))
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        drawn_ids = set(engine.state.players["A"].zones["hand"]) - hand_before
        self.assertEqual(1, len(drawn_ids))
        drawn_ref = engine.state.cards[next(iter(drawn_ids))].ref
        self.assertIn(drawn_ref, json.dumps(session.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(drawn_ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        self.replay(session, load=True)
        # A condition cached before the prefix would miss the draw.
        source = self.add(engine, "Generic Condition Treasure Draw", zone="hand", ref="mutant-condition")
        action = self.ready(session, source, {"G": 1})
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        with patch.object(engine, "_fixed_public_state_condition_holds", return_value=False):
            self.resolve(session)
        self.assertEqual(0, sum(event.code == "card.draw" for event in session.state.events) - draws)

    def test_trusted_death_prefix_updates_history_before_condition_and_replays(self):
        session = self.session(4821002)
        engine = session.engine
        source = self.add(engine, "Generic Condition Death Draw", zone="hand")
        target = self.add(engine, "Generic Bound Body", seat="B", ref="death-recipient")
        action = self.ready(session, source, {"B": 1})
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.checkpoint(session)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", engine.state.cards[target.object_id].zone)
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.assertEqual(1, sum(event.kind == "creature_died" for event in engine.state.turn_history.events))
        self.replay(session, load=True)

    def test_private_conditional_scry_resumes_without_repeating_prefix(self):
        session = self.session(4821003)
        engine = session.engine
        self.add(engine, "Generic Bound Mentor", ref="artifact-condition")
        source = self.add(engine, "Generic Condition Draw Scry", zone="hand")
        action = self.ready(session, source, {"U": 1})
        self.checkpoint(session)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(session)
        self.assertEqual("semantic.choice", decision.kind)
        self.assertEqual(1, sum(event.code == "card.draw" for event in engine.state.events) - draws)
        refs = tuple(engine.state.cards[object_id].ref for object_id in reversed(engine.state.players["A"].zones["library"][-2:]))
        for seat in "BCD":
            self.assertTrue(all(ref not in json.dumps(session.packet(f"pilot:{seat}", full=True)) for ref in refs))
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "cards": {"top": [refs[0]], "bottom": []}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "cards": {"top": list(refs), "bottom": []}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.replay(session, load=True)

    def test_trusted_activation_checks_resolution_controller_and_false_result(self):
        session = self.session(4821004)
        engine = session.engine
        source = self.add(engine, "Generic Condition Activated Draw")
        action = self.ready(session, source, {})
        self.checkpoint(session)
        life_before = engine.state.players["A"].life
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(engine.state.cards[source.object_id].tapped)
        self.assertEqual(life_before, engine.state.players["A"].life)
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.replay(session, load=True)

    def test_trusted_sacrificed_source_uses_ability_controller_not_graveyard_owner(self):
        session = self.session(4821005)
        engine = session.engine
        source = self.add(engine, "Generic Condition Sacrificed Draw")
        source.owner = "B"
        self.add(engine, "Generic Bound Mentor", ref="resolver-artifact")
        action = self.ready(session, source, {"C": 1})
        self.checkpoint(session)
        life_before = engine.state.players["A"].life
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        departed = engine.state.cards[source.object_id]
        self.assertEqual("graveyard", departed.zone)
        self.assertEqual("B", departed.controller)
        self.assertEqual("A", engine.state.stack[-1].controller)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life_before + 3, engine.state.players["A"].life)
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.replay(session, load=True)

    def test_existing_reanimation_leaf_composes_with_token_result_and_replays(self):
        session = self.session(4821006)
        engine = session.engine
        source = self.add(engine, "Generic Composed Return Food", zone="hand")
        target = self.add(engine, "Generic Bound Body", zone="graveyard", ref="return-recipient")
        incarnation = target.logical_object_id
        action = self.ready(session, source, {"W": 1})
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        returned = engine.state.cards[target.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(incarnation, returned.logical_object_id)
        self.assertEqual("A", returned.controller)
        foods = [card for card in engine.state.cards.values() if card.is_token and card.zone == "battlefield"
                 and "food" in engine._type_parts(engine._effective_card_data(card)["type_line"])[1]]
        self.assertEqual(1, len(foods))
        self.replay(session, load=True)

    def test_trusted_subtype_condition_counts_noncreature_kindred_and_replays(self):
        session = self.session(4821007)
        engine = session.engine
        self.add(engine, "Generic Condition Kindred Faerie", ref="kindred-faerie")
        source = self.add(engine, "Generic Condition Faerie Draw", zone="hand")
        target = self.add(engine, "Generic Bound Body", ref="faerie-test-target")
        action = self.ready(session, source, {"B": 1})
        self.checkpoint(session)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()
