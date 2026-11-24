"""A fixed Wolf result and the sealed damaged player's ordinary mill."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.mill_templates import EQUIPPED_WOLF_MILL_MECHANIC
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Sword of Body and Mind"


class EquippedCombatWolfAndMillTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "equipped-combat-wolf-and-mill.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/equipped-combat-wolf-and-mill-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped combat Wolf and Mill", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_two_fixed_results_and_original_attachment_scope(self):
        outcome, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(outcome["strict_capability_ready"], outcome["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        trigger, = (row for row in programs if row.event == "damage.dealt")
        self.assertGreater(len(programs), 1)
        self.assertIsNone(trigger.target_schema)
        self.assertIn(EQUIPPED_WOLF_MILL_MECHANIC, trigger.coverage)
        token, mill = trigger.effects
        self.assertEqual(("create_token", "$controller", 1), (token["op"], token["controller"], token["quantity"]))
        self.assertEqual({"op": "mill", "player": "$context.target", "count": 10}, mill)
        for changes in (
            {"effects": [token]}, {"effects": [mill, token]},
            {"effects": [token, {**mill, "player": "$controller"}]},
            {"effects": [{**token, "quantity": 2}, mill]},
        ):
            result = bind_semantic_program_runtime(
                replace(trigger, **changes), capability_registry=self.registry, profile="commander_review",
            )
            self.assertFalse(result["strict"], result)
        for text in (
            record.oracle_text.replace("Wolf", "Bear"),
            record.oracle_text.replace("ten cards", "nine cards"),
            record.oracle_text.replace("combat damage", "damage"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(record, oracle_text=text), capability_registry=self.registry,
            ).status)

    def scene(self, seed, *, doubled=False, short=False, lethal=False):
        game = self.session(seed)
        sword = self.add(game.engine, ORIGINAL, zone="hand")
        carrier = self.add(game.engine, "Runeclaw Bear")
        carrier.acquired_control_turn_count = -1
        if doubled:
            self.assert_closed(game, self.add(game.engine, "Doubling Season"))
        if short:
            for object_id in tuple(game.state.players["B"].zones["library"][:-2]):
                game.engine.move_card(object_id, "exile", log=False, semantic_events=False)
        if lethal:
            game.state.players["B"].life = 4
        self.assert_closed(game, sword)
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        self.cast(game, "A", sword)
        self.resolve_top(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip = next(row for row in actions if row["id"].startswith(f"activate:{sword.ref}:"))
        result = game.act("pilot:A", {"action_id": equip["id"], "targets": [carrier.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(carrier.object_id, game.state.cards[sword.object_id].attached_to)
        self.assertEqual(4, game.engine._numeric_stat(carrier.object_id, "power"))
        self.attack(game, [(carrier, "B")])
        self.until(game, lambda current: self.trigger_count(current, sword) == 1)
        return game

    def wolves(self, game):
        found = []
        for card in game.state.cards.values():
            if card.zone != "battlefield" or not card.is_token:
                continue
            data = game.engine._effective_card_data(card)
            if "wolf" in game.engine._type_parts(data["type_line"])[1]:
                self.assertEqual(("A", "A"), (card.owner, card.controller))
                self.assertEqual({"G"}, set(data["colors"]))
                self.assertEqual((2, 2), tuple(game.engine._numeric_stat(card.object_id, stat)
                                              for stat in ("power", "toughness")))
                found.append(card)
        return found

    def test_actual_token_replacement_creates_two_wolves_before_the_damaged_player_mills_ten(self):
        game = self.scene(70117021, doubled=True)
        self.assertEqual(36, game.state.players["B"].life)
        sizes = {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(self.wolves(game)))
        self.assertEqual({**sizes, "B": sizes["B"] - 10},
                         {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"})
        events = game.state.events[cursor:]
        codes = [event.code for event in events]
        self.assertLess(codes.index("token.create"), codes.index("card.mill"))
        mill, = (event for event in events if event.code == "card.mill")
        self.assertEqual(10, mill.details["count"])
        self.replay(game, load=True)

    def test_actual_short_damaged_library_mills_all_available_and_retains_the_wolf(self):
        game = self.scene(70117022, short=True)
        expected = set(game.state.players["B"].zones["library"])
        before_graveyard = set(game.state.players["B"].zones["graveyard"])
        self.assertEqual(2, len(expected))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.wolves(game)))
        self.assertEqual([], game.state.players["B"].zones["library"])
        self.assertEqual(expected, set(game.state.players["B"].zones["graveyard"]) - before_graveyard)
        self.assertIn("B", game.state.active_seats())
        self.replay(game, load=True)

    def test_actual_lethal_damage_still_creates_a_wolf_when_the_damaged_player_leaves(self):
        game = self.scene(70117023, lethal=True)
        self.assertNotIn("B", game.state.active_seats())
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.wolves(game)))
        self.assertFalse(any(event.code == "card.mill" for event in game.state.events[cursor:]))
        self.replay(game, load=True)
