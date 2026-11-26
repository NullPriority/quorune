"""Grouped combat damage taps the sealed recipient's lands before untapping ours."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Nature's Will"


class CombatRecipientLandTapUntapTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "combat-recipient-land-tap-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/combat-recipient-land-tap-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Combat recipient land tap and untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_two_lands_only_sets_in_the_printed_order(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual("damage.dealt", program.event)
        self.assertIn("one_or_more_event_batch", program.coverage)
        self.assertIsNone(program.target_schema)
        tap, untap = program.effects
        self.assertEqual(["set_public_tap_state", "set_public_tap_state"], [tap["op"], untap["op"]])
        self.assertEqual((True, "$context.target"), (tap["tapped"], tap["set"]["target_controller"]))
        self.assertEqual((False, "actor"), (untap["tapped"], untap["set"]["controller_relation"]))
        self.assertEqual([["land"], ["land"]], [leaf["set"]["query"]["types_all"] for leaf in program.effects])
        self.assertIn("permanent.tap_state.fixed_set", program.capability_dependencies)
        for weaker in (replace(program, effects=[untap, tap]), replace(program, effects=[tap])):
            self.assertFalse(bind_semantic_program_runtime(
                weaker, capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def scene(self, seed, attackers, *, lethal=False):
        game = self.session(seed)
        enchantment = self.add(game.engine, ORIGINAL, zone="hand")
        own = [self.add(game.engine, name, ref=f"combat-tap-own-{i}")
               for i, name in enumerate(("Forest", "Forest", "Wastes", "Wastes"))]
        damaged = [self.add(game.engine, "Island", seat="B", ref=f"damaged-land-{i}") for i in range(2)]
        unrelated = self.add(game.engine, "Forest", seat="C", ref="unrelated-land")
        foreign_creature = self.add(game.engine, "Runeclaw Bear", seat="B", ref="unrelated-battlefield-creature")
        attackers = [self.add(game.engine, name, ref=f"combat-tap-attacker-{i}") for i, name in enumerate(attackers)]
        for attacker in attackers:
            attacker.acquired_control_turn_count = -1
        if lethal:
            game.state.players["B"].life = 2
        self.assert_closed(game, enchantment)
        self.prepare(game)
        self.checkpoint(game)
        self.cast(game, "A", enchantment)
        self.resolve_top(game)
        self.assertEqual([True] * len(own), [game.state.cards[land.object_id].tapped for land in own])
        self.attack(game, [(attacker, "B") for attacker in attackers])
        self.until(game, lambda current: self.trigger_count(current, enchantment) == 1)
        self.assertEqual(("B", []), (game.state.stack[-1].context["target"], game.state.stack[-1].targets))
        return game, enchantment, own, damaged, unrelated, foreign_creature

    def tap_for_mana(self, game, seat, land, output, *, reject_wrong_actor=False):
        self.priority(game, seat)
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith(f"activate:{land.ref}:")]
        command = {"action_id": action["id"], "mana_output": output}
        if reject_wrong_actor:
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:D", command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_same_recipient_group_taps_only_their_lands_then_untaps_ours(self):
        game, source, own, damaged, unrelated, creature = self.scene(70126051, ("Runeclaw Bear", "Runeclaw Bear"))
        self.assertEqual(36, game.state.players["B"].life)
        self.tap_for_mana(game, "B", damaged[0], {"U": 1}, reject_wrong_actor=True)
        game = self.replay(game, load=True)
        before = len(game.state.events)
        self.resolve_top(game)
        self.assertEqual([True, True], [game.state.cards[row.object_id].tapped for row in damaged])
        self.assertEqual([False] * len(own), [game.state.cards[row.object_id].tapped for row in own])
        self.assertFalse(game.state.cards[unrelated.object_id].tapped)
        self.assertFalse(game.state.cards[creature.object_id].tapped)
        self.assertEqual(1, game.state.players["B"].mana_pool["U"])
        self.assertEqual([True, False], [
            event.details["tapped"] for event in game.state.events[before:]
            if event.code == "effect.permanent.tap_state_set"
        ])
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_actual_double_strike_resolves_one_group_in_each_damage_step(self):
        game, source, own, _, _, _ = self.scene(70126052, ("Fencing Ace",))
        self.assertEqual(39, game.state.players["B"].life)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[own[0].object_id].tapped)
        self.tap_for_mana(game, "A", own[0], {"G": 1})
        self.assertTrue(game.state.cards[own[0].object_id].tapped)
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        self.assertEqual(38, game.state.players["B"].life)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[own[0].object_id].tapped)
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_actual_lethal_damage_can_still_untap_the_retained_controller_lands(self):
        game, _, own, _, unrelated, _ = self.scene(70126053, ("Runeclaw Bear",), lethal=True)
        self.assertNotIn("B", game.state.active_seats())
        game = self.replay(game, load=True)
        before = len(game.state.events)
        self.resolve_top(game)
        self.assertEqual([False] * len(own), [game.state.cards[row.object_id].tapped for row in own])
        self.assertFalse(game.state.cards[unrelated.object_id].tapped)
        self.assertEqual([False], [
            event.details["tapped"] for event in game.state.events[before:]
            if event.code == "effect.permanent.tap_state_set"
        ])
        self.replay(game, load=True)
