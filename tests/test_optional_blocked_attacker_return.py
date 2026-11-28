"""A sealed newly blocked attacker can return before damage, with separate grouped draw."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.combat_removal import OPTIONAL_BLOCKED_ATTACKER_RETURN_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Grazilaxx, Illithid Scholar"


class OptionalBlockedAttackerReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-blocked-attacker-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/optional-blocked-attacker-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Blocked attacker return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_blocked_attacker_and_separate_damage_batch(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        return_trigger, draw = sorted(semantics.programs_for_oracle(record.oracle_id), key=lambda row: row.event)
        self.assertEqual(("creature.becomes_blocked", "damage.dealt"), (return_trigger.event, draw.event))
        self.assertIsNone(return_trigger.target_schema)
        self.assertEqual({"field": "controller", "op": "eq", "value": "$source.controller"}, return_trigger.event_condition)
        return_option, = return_trigger.effects
        self.assertEqual(("offer_optional_effect", "$controller"), (return_option["op"], return_option["player"]))
        self.assertEqual([{"op": "bounce", "card": "$event.permanent_object"}], return_option["effects"])
        self.assertIn("permanent.return.owner_hand", return_trigger.capability_dependencies)
        self.assertIn("trigger.event.normalized_public_action", return_trigger.capability_dependencies)
        self.assertEqual(OPTIONAL_BLOCKED_ATTACKER_RETURN_VERSION, return_option["schema_version"])
        stripped = replace(return_trigger, event_condition={"field": "card", "op": "eq", "value": "$source.ref"})
        self.assertFalse(bind_semantic_program_runtime(
            stripped, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        self.assertIn("one_or_more_event_batch", draw.coverage)
        self.assertEqual([{"op": "draw", "player": "$controller", "count": 1, "private": True}], draw.effects)

    def cast_source(self, game, source, *, retain_treason=False):
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "R": 1, "U": 2} if retain_treason else {"C": 1, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 2, "R": 1} if retain_treason else None)
        self.assertIsNone(self.resolve(game))

    def blocked_scene(self, seed, *, borrow=False, response=False):
        game = self.session(seed)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        attacker = self.add(game.engine, "Runeclaw Bear", seat="B" if borrow else "A", ref="original-blocked-attacker")
        attacker.acquired_control_turn_count = -1
        blockers = [self.add(game.engine, "Phyrexian Walker", seat="B", ref=f"walker-{i}") for i in range(2)]
        other = self.add(game.engine, "Runeclaw Bear", ref="other-controlled-creature")
        theft = self.add(game.engine, "Act of Treason", zone="hand") if borrow else None
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand") if response else None
        if response:
            self.add(game.engine, "Island", seat="B")
            self.assert_closed(game, bounce)
        if borrow:
            self.assert_closed(game, theft)
        self.cast_source(game, source, retain_treason=borrow)
        if borrow:
            self.cast_for(game, "A", theft, [attacker.ref])
            self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.cards[attacker.object_id].controller)
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        command = {"action_id": "block", "blocks": {blocker.ref: attacker.ref for blocker in blockers}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        self.assertFalse(game.state.stack[-1].targets)
        self.assertEqual("declare_blockers", game.state.step)
        return game, source, attacker, blockers, other, bounce

    def test_actual_multiple_blockers_offer_one_owned_return_before_combat_damage(self):
        game, source, attacker, blockers, other, _ = self.blocked_scene(50903021, borrow=True)
        original = game.state.cards[attacker.object_id].logical_object_id
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        private = game.state.cards[game.state.players["D"].zones["hand"][0]].ref
        self.assertNotIn(private, json.dumps(game.packet("pilot:A", full=True)))
        command = {"action_id": "choose", "choice": "apply"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        returned = game.state.cards[attacker.object_id]
        self.assertEqual(("B", "hand"), (returned.owner, returned.zone))
        self.assertIn(attacker.object_id, game.state.players["B"].zones["hand"])
        self.assertNotEqual(original, returned.logical_object_id)
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.assertEqual([0, 0], [game.state.cards[card.object_id].marked_damage for card in blockers])
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_actual_decline_preserves_combat_and_departed_attacker_needs_no_choice(self):
        for index, response in enumerate((False, True)):
            with self.subTest(removed=response):
                game, _, attacker, blockers, _, bounce = self.blocked_scene(50903022 + index, response=response)
                if response:
                    self.cast_for(game, "B", bounce, [attacker.ref])
                    self.resolve_top(game)
                    game = self.replay(game, load=True)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
                else:
                    self.assertEqual("semantic.choice", self.resolve(game).kind)
                    action = game.act("pilot:A", {"action_id": "choose", "choice": "decline"})
                    self.assertTrue(action.ok, action.summary)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("battlefield", game.state.cards[attacker.object_id].zone)
                    self.assertIn(attacker.object_id, game.state.combat.attackers)
                self.assertEqual([0, 0], [game.state.cards[card.object_id].marked_damage for card in blockers])
                self.replay(game, load=True)

    def test_actual_two_damaged_players_draw_twice_even_from_three_unblocked_attackers(self):
        game = self.session(50903024)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        attackers = [self.add(game.engine, "Runeclaw Bear", ref=f"unblocked-bear-{i}") for i in range(3)]
        for attacker in attackers:
            attacker.acquired_control_turn_count = -1
        self.cast_source(game, source)
        before = len(game.state.players["A"].zones["hand"])
        self.attack(game, list(zip(attackers, ("B", "B", "C"))))
        self.until(game, lambda current: self.trigger_count(current, source) == 2)
        self.assertEqual((36, 38), (game.state.players["B"].life, game.state.players["C"].life))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 2, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)
