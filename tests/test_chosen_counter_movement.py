"""Original target announcement, counter choice, and atomic movement for a paid land activation."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_counter_movement_model import (
    CHOSEN_COUNTER_MOVEMENT_CAPABILITY,
    FixedCounterMovementSpec,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_counter_movement import fixed_counter_movement_target_schema
from quorune.semantic_runtime.activated_abilities import activated_abilities_from_descriptors
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_fixed_one_counter_movement as movement_fixture


PROFILE = "commander_review"


class ChosenCounterMovementTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.FixedOneCounterMovementTests.reject
    ready = movement_fixture.FixedOneCounterMovementTests.ready
    activate = movement_fixture.FixedOneCounterMovementTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-counter-movement.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "chosen-counter-movement-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Chosen counter movement", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_nesting_grounds_requires_the_printed_price_and_two_ordered_public_targets(self):
        card = self.db.lookup("Nesting Grounds", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        activate, = (program for program in programs if CHOSEN_COUNTER_MOVEMENT_CAPABILITY in program.capability_dependencies)
        ability, = activated_abilities_from_descriptors(activate.handlers)
        self.assertTrue(ability.sorcery_speed)
        self.assertTrue(ability.tap_source)
        self.assertEqual({"GENERIC": 1}, {key: n for key, n in ability.mana.items() if n})
        spec = FixedCounterMovementSpec.from_effect(activate.effects[0])
        self.assertTrue(spec.chooses_counter_kind)
        self.assertEqual(fixed_counter_movement_target_schema(spec), activate.target_schema)
        self.assertEqual("you", activate.target_schema["groups"][0]["controller_relation"])
        self.assertNotIn("controller_relation", activate.target_schema["groups"][1])
        dependencies = tuple(cap for cap in activate.capability_dependencies if cap != CHOSEN_COUNTER_MOVEMENT_CAPABILITY)
        for changed in (
            replace(activate, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict()),
            replace(activate, cost_schema={**activate.cost_schema, "tap_source": False}),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for original, different in (
            ("a counter", "any number of counters"),
            ("you control", "an opponent controls"),
            (" Activate only as a sorcery.", ""),
        ):
            with self.subTest(different=different):
                changed = replace(card, oracle_text=card.oracle_text.replace(original, different, 1))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)

    def offer(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"]
        offer, = actions
        return offer["choice_schema"]

    def test_actual_chosen_counter_moves_between_distinct_permanents_after_the_paid_activation(self):
        game = self.session(76551001)
        source = self.add(game.engine, "Nesting Grounds")
        mana = self.add(game.engine, "Forest")
        donor = self.add(game.engine, "Plains", ref="chosen-move-donor")
        donor.tapped = True
        donor.counters.update({"charge": 2, "quest": 1})
        recipient = self.add(game.engine, "Island", seat="B")
        self.ready(game, source)
        action_id = f"activate:{source.ref}:ab2"
        for targets in ([donor.ref, donor.ref], [recipient.ref, donor.ref]):
            self.reject(game, "A", {"action_id": action_id, "targets": targets, "pay": "auto"})
        self.reject(game, "B", {"action_id": action_id, "targets": [donor.ref, recipient.ref], "pay": "auto"})
        self.activate(game, source, "ab2", [donor.ref, recipient.ref])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertTrue(game.state.cards[mana.object_id].tapped)
        schema = self.offer(game)
        self.assertEqual("counter_kind", schema["field"])
        self.assertEqual(["charge", "quest"], schema["legal_values"])
        command = {"action_id": "choose", "counter_kind": "charge"}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "counter_kind": "+1/+1"})
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"charge": 1, "quest": 1}, game.state.cards[donor.object_id].counters)
        self.assertEqual({"charge": 1}, game.state.cards[recipient.object_id].counters)
        self.replay(game, load=True)

    def test_chosen_movement_native_witness_detects_a_substituted_counter_kind(self):
        from quorune.semantic_choices import fixed_counter_movement

        original = fixed_counter_movement._intent

        def fixed_only(effect, stack_ref, *, counter_name="+1/+1"):
            return original(effect, stack_ref)

        with patch.object(fixed_counter_movement, "_intent", side_effect=fixed_only):
            with self.assertRaisesRegex(AssertionError, "charge"):
                self.test_actual_chosen_counter_moves_between_distinct_permanents_after_the_paid_activation()

    def test_actual_chosen_counter_replacement_retains_the_kind_and_waits_before_removal(self):
        game = self.session(76551002)
        source = self.add(game.engine, "Nesting Grounds")
        self.add(game.engine, "Forest")
        donor = self.add(game.engine, "Grizzly Bears", ref="chosen-replacement-donor")
        donor.counters.update({"+1/+1": 2, "charge": 1})
        recipient = self.add(game.engine, "Grizzly Bears", seat="B", ref="chosen-replacement-recipient")
        scales = self.add(game.engine, "Hardened Scales", seat="B")
        season = self.add(game.engine, "Doubling Season", seat="B")
        self.ready(game, source, scales, season)
        self.activate(game, source, "ab2", [donor.ref, recipient.ref])
        self.assertEqual(["+1/+1", "charge"], self.offer(game)["legal_values"])
        result = game.act("pilot:A", {"action_id": "choose", "counter_kind": "+1/+1"})
        self.assertTrue(result.ok, result.summary)
        decision = game.state.pending_decision
        self.assertEqual(("replacement.order", ["B"]), (decision.kind, decision.actors))
        self.assertEqual({"+1/+1": 2, "charge": 1}, game.state.cards[donor.object_id].counters)
        self.assertFalse(game.state.cards[recipient.object_id].counters)
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        choice, = (option["id"] for option in options if option["source"] == scales.ref)
        result = game.act("pilot:B", {"action_id": "choose", "replacement": choice})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 1, "charge": 1}, game.state.cards[donor.object_id].counters)
        self.assertEqual({"+1/+1": 4}, game.state.cards[recipient.object_id].counters)
        self.replay(game, load=True)

    def test_actual_chosen_movement_can_target_an_empty_donor_and_never_uses_a_departed_recipient(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(76551005 + departed)
                source = self.add(game.engine, "Nesting Grounds")
                self.add(game.engine, "Forest")
                donor = self.add(game.engine, "Grizzly Bears", ref="chosen-current-donor")
                recipient = self.add(game.engine, "Grizzly Bears", seat="B", ref="chosen-current-recipient")
                if departed:
                    donor.counters["charge"] = 1
                    bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                    self.add(game.engine, "Island", seat="B")
                    self.assert_closed(game, bounce)
                self.ready(game, source)
                self.activate(game, source, "ab2", [donor.ref, recipient.ref])
                if departed:
                    self.cast_for(game, "B", bounce, [recipient.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertTrue(game.state.cards[source.object_id].tapped)
                self.assertEqual(int(departed), game.state.cards[donor.object_id].counters.get("charge", 0))
                self.assertEqual("hand" if departed else "battlefield", game.state.cards[recipient.object_id].zone)
                self.assertFalse(game.state.cards[recipient.object_id].counters)
                self.replay(game, load=True)
