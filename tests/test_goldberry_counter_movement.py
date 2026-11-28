"""Native public counter movement and private committed draw from the original activations."""

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
from quorune.fixed_counter_movement_model import SOURCE_COUNTER_MOVEMENT_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.activated_abilities import activated_abilities_from_descriptors
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_fixed_one_counter_movement as movement


PROFILE = "commander_review"
NAME = "Goldberry, River-Daughter"


class GoldberryCounterMovementTests(InheritedNativeFixture, unittest.TestCase):
    activate = movement.FixedOneCounterMovementTests.activate
    reject = movement.FixedOneCounterMovementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "goldberry.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "chosen-counter-movement-cards.json",
            fixtures / "resourceful-defense-cards.json",
            fixtures / "counter-replacement-cards.json",
            fixtures / "goldberry-counter-movement-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Goldberry counter movement", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active="A", mana=mana)
        self.checkpoint(game)

    def test_printed_roles_and_activation_prices_fail_closed(self):
        card = self.db.lookup(NAME, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = [row for row in semantics.programs_for_oracle(card.oracle_id)
                    if SOURCE_COUNTER_MOVEMENT_CAPABILITY in row.capability_dependencies]
        self.assertEqual(2, len(programs))
        for row in programs:
            self.assertTrue(row.target_schema["source_exclusion"])
            self.assertEqual("you", row.target_schema["controller_relation"])
            ability, = activated_abilities_from_descriptors(row.handlers)
            self.assertTrue(ability.tap_source)
            dependencies = tuple(value for value in row.capability_dependencies
                                 if value != SOURCE_COUNTER_MOVEMENT_CAPABILITY)
            incomplete = replace(row, capability_dependencies=dependencies,
                                 capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
            self.assertFalse(bind_semantic_program_runtime(
                incomplete, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for old, new in (("{U}, {T}", "{1}, {T}"), ("another target", "target"),
                         ("not on Goldberry", "on Goldberry"), ("draw a card", "gain 1 life")):
            with self.subTest(old=old):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new, 1))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)

    def test_actual_first_activation_moves_one_each_missing_kind_between_original_permanents(self):
        game = self.session(47839501)
        source = self.add(game.engine, NAME)
        donor = self.add(game.engine, "Forest")
        opponent = self.add(game.engine, "Island", seat="B")
        source.counters["charge"] = 1
        donor.counters.update({"+1/+1": 2, "charge": 3, "quest": 2})
        self.start(game, source)
        command = {"action_id": f"activate:{source.ref}:ab1", "pay": "auto"}
        for target in (source, opponent):
            self.reject(game, "A", {**command, "targets": [target.ref]})
        self.activate(game, source, "ab1", [donor.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 1, "charge": 1, "quest": 1}, game.state.cards[source.object_id].counters)
        self.assertEqual({"+1/+1": 1, "charge": 3, "quest": 1}, game.state.cards[donor.object_id].counters)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.replay(game, load=True)

    def _amount(self, game, kind, minimum, maximum):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        visible = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertIn(kind, visible["prompt"])
        action, = visible["legal_actions"]
        schema = action["choice_schema"]
        self.assertEqual((minimum, maximum), (schema["minimum"], schema["maximum"]))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])

    def test_actual_second_activation_moves_quantities_and_draws_once_after_commit(self):
        game = self.session(47839502)
        source = self.add(game.engine, NAME)
        recipient = self.add(game.engine, "Forest")
        source.counters.update({"charge": 2, "quest": 2})
        self.start(game, source, mana={"U": 1})
        hand_count = len(game.state.players["A"].zones["hand"])
        self.activate(game, source, "ab2", [recipient.ref])
        self._amount(game, "charge", 0, 2)
        command = {"action_id": "choose", "amount": 0}
        self.reject(game, "B", command)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self._amount(game, "quest", 1, 2)
        self.reject(game, "A", command)
        game = self.replay(game, load=True)
        self.assertEqual(hand_count, len(game.state.players["A"].zones["hand"]))
        result = game.act("pilot:A", {**command, "amount": 2})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"charge": 2}, game.state.cards[source.object_id].counters)
        self.assertEqual({"quest": 2}, game.state.cards[recipient.object_id].counters)
        self.assertEqual(hand_count + 1, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_second_activation_does_not_draw_when_original_source_leaves_or_lacks_counters(self):
        for bounced in (False, True):
            with self.subTest(bounced=bounced):
                game = self.session(47839503 + int(bounced))
                source = self.add(game.engine, NAME)
                recipient = self.add(game.engine, "Forest")
                if bounced:
                    self.add(game.engine, "Island", seat="B")
                    bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
                    source.counters["charge"] = 2
                self.start(game, source, *([bounce] if bounced else []), mana={"U": 1})
                hand_count = len(game.state.players["A"].zones["hand"])
                self.activate(game, source, "ab2", [recipient.ref])
                if bounced:
                    self.cast(game, "B", bounce, [source.ref])
                    self.resolve_top(game)
                self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.cards[recipient.object_id].counters)
                self.assertEqual(hand_count + int(bounced), len(game.state.players["A"].zones["hand"]))
                self.replay(game, load=True)

    def test_actual_second_activation_detects_omitting_committed_draw(self):
        from quorune.semantic_choices import counter_movement_quantities

        original = counter_movement_quantities._counter_set_intent

        def without_draw(effect, amounts, stack_ref):
            return replace(original(effect, amounts, stack_ref), mode=None)

        with patch.object(counter_movement_quantities, "_counter_set_intent", side_effect=without_draw):
            with self.assertRaisesRegex(AssertionError, r"\d+ != \d+"):
                self.test_actual_second_activation_moves_quantities_and_draws_once_after_commit()

    def test_actual_second_activation_draws_after_a_valid_payment_replaced_to_zero(self):
        game = self.session(47839505)
        source = self.add(game.engine, NAME)
        recipient = self.add(game.engine, "Forest")
        replacement = self.add(game.engine, "Vorinclex, Monstrous Raider", seat="B")
        source.counters["charge"] = 1
        self.start(game, source, replacement, mana={"U": 1})
        hand_count = len(game.state.players["A"].zones["hand"])
        self.activate(game, source, "ab2", [recipient.ref])
        self._amount(game, "charge", 1, 1)
        result = game.act("pilot:A", {"action_id": "choose", "amount": 1})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"charge": 1}, game.state.cards[source.object_id].counters)
        self.assertFalse(game.state.cards[recipient.object_id].counters)
        self.assertEqual(hand_count + 1, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)
