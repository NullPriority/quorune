"""The equipped combat trigger reads two live hand sizes through their own roles."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Sword of War and Peace"


class EquippedCombatHandSizesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-combat-hand-sizes.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/equipped-combat-hand-sizes-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped combat hand sizes", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_ordered_distinct_live_hand_roles(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        trigger, = (p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "damage.dealt")
        self.assertIsNone(trigger.target_schema)
        damage, life = trigger.effects
        self.assertEqual(("damage", "$source", "$context.target"), (damage["op"], damage["source"], damage["target"]))
        self.assertEqual(("life", "$controller"), (life["op"], life["player"]))
        self.assertEqual("damaged_player", damage["amount"]["role"])
        self.assertEqual("trigger_controller", life["delta"]["role"])
        for effects in ([life, damage], [damage], [{**damage, "amount": life["delta"]}, life]):
            self.assertFalse(bind_semantic_program_runtime(
                replace(trigger, effects=effects), capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def setup_game(self, seed, *, responses=False, departure=False, empty=False, lethal=False):
        game = self.session(seed)
        sword = self.add(game.engine, ORIGINAL, zone="hand")
        carrier = self.add(game.engine, "Runeclaw Bear")
        carrier.acquired_control_turn_count = -1
        grow = ritual = removal = private = None
        if responses:
            grow = self.add(game.engine, "Giant Growth", zone="hand")
            ritual = self.add(game.engine, "Dark Ritual", seat="B", zone="hand")
            private = self.add(game.engine, "Island", seat="B", zone="hand", ref="unrevealed-hand-count-card")
            self.add(game.engine, "Forest", ref="hand-size-green-mana")
            self.add(game.engine, "Swamp", seat="B")
            self.assert_closed(game, grow)
            self.assert_closed(game, ritual)
        if departure:
            removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
            self.add(game.engine, "Plains", seat="B")
            self.add(game.engine, "Wastes", seat="B")
            self.assert_closed(game, removal)
        if empty:
            for object_id in tuple(game.state.players["B"].zones["hand"]):
                game.engine.move_card(object_id, "library", log=False, semantic_events=False)
        if lethal:
            game.state.players["B"].life = 4
        self.assert_closed(game, sword)
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        self.cast_for(game, "A", sword, reserve={"C": 2})
        self.resolve_top(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = [row for row in actions if row["id"].startswith(f"activate:{sword.ref}:")]
        result = game.act("pilot:A", {"action_id": equip["id"], "targets": [carrier.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(carrier.object_id, game.state.cards[sword.object_id].attached_to)
        self.attack(game, [(carrier, "B")])
        self.until(game, lambda current: self.trigger_count(current, sword) == 1)
        self.assertFalse(game.state.stack[-1].targets)
        return game, sword, carrier, grow, ritual, removal, private

    def sizes(self, game):
        return {seat: len(game.state.players[seat].zones["hand"]) for seat in "AB"}

    def test_actual_responses_change_each_live_amount_without_revealing_private_cards(self):
        game, _, carrier, grow, ritual, _, private = self.setup_game(70216061, responses=True)
        before = self.sizes(game)
        self.cast_for(game, "A", grow, [carrier.ref])
        self.resolve_top(game)
        self.cast_for(game, "B", ritual)
        self.resolve_top(game)
        now = self.sizes(game)
        self.assertEqual({seat: n - 1 for seat, n in before.items()}, now)
        self.assertEqual(36, game.state.players["B"].life)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((40 + now["A"], 36 - now["B"]),
                         (game.state.players["A"].life, game.state.players["B"].life))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_departed_equipment_preserves_damage_then_its_original_controller_gain(self):
        game, source, _, _, _, removal, _ = self.setup_game(70216062, departure=True)
        self.cast_for(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        now = self.sizes(game)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((40 + now["A"], 36 - now["B"]),
                         (game.state.players["A"].life, game.state.players["B"].life))
        self.replay(game, load=True)

    def test_actual_empty_or_departed_recipient_still_allows_the_controller_gain(self):
        for index, lethal in enumerate((False, True)):
            with self.subTest(lethal=lethal):
                game, _, _, _, _, _, _ = self.setup_game(70216063 + index, empty=not lethal, lethal=lethal)
                self.assertEqual(not lethal, game.state.players["B"].in_game)
                now = self.sizes(game)
                self.assertEqual(0, now["B"])
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(40 + now["A"], game.state.players["A"].life)
                if not lethal:
                    self.assertEqual(36, game.state.players["B"].life)
                self.replay(game, load=True)
