"""Native Food production from rounded sealed entry X and optional attack payment."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_x_amount_model import ENTRY_HALF_X_FOOD_CAPABILITY, EntryXAmountSpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class RoundedEntryFoodTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "rounded-entry-food.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/rounded-entry-food-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Entry Food", [DeckEntry(commander, 1, "commander"),
                                                 DeckEntry("Plains", 30)], [commander])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def accepted(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def rejected(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def foods(self, game):
        return [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token
                and "food" in game.engine._effective_type_parts(card)[1]]

    def test_rounded_entry_food_is_closed_and_different_rounding_stays_residual(self):
        card = self.db.lookup("The Goose Mother", fuzzy=False)
        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        exact = compile_card(card)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        rounded, = [node for face in exact.faces for node in face.nodes
                    if ENTRY_HALF_X_FOOD_CAPABILITY in node.capability_dependencies]
        self.assertEqual(EntryXAmountSpec(schema_version=2).to_dict(), rounded.effects[0]["quantity"])
        for oracle in (card.oracle_text.replace("rounded up", "rounded down"),
                       card.oracle_text.replace("Food tokens, rounded", "Clue tokens, rounded"),
                       card.oracle_text.replace("enters, create half", "attacks, create half")):
            with self.subTest(oracle=oracle):
                self.assertNotEqual("exact", compile_card(replace(card, oracle_text=oracle)).status)

    def test_actual_goose_entry_rounds_odd_and_even_x_and_replays_after_source_departure(self):
        for x in (2, 3):
            with self.subTest(x=x):
                game = self.session(107300510 + x)
                source = self.add(game.engine, "The Goose Mother", zone="hand")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.add(game.engine, "Hardened Scales")
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": x, "G": 1, "U": 1})
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                self.accepted(game, "A", {"action_id": f"cast:{source.ref}", "x": x, "pay": "auto"})
                self.resolve_top(game)
                self.assertEqual(x + 1, game.state.cards[source.object_id].counters["+1/+1"])
                self.assertFalse(self.foods(game))
                game = self.replay(game, load=True)
                if x == 3:
                    self.cast(game, "B", bounce, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[source.object_id].zone)
                self.resolve_top(game)
                self.assertEqual((x + 1) // 2, len(self.foods(game)))
                self.replay(game, load=True)

    def test_actual_goose_blink_without_a_cast_uses_zero_food_and_counters(self):
        game = self.session(107300520)
        source = self.add(game.engine, "The Goose Mother")
        source.counters["+1/+1"] = 3
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        previous = source.logical_object_id
        self.prepare(game, mana={"W": 1})
        self.checkpoint(game)
        self.cast(game, "A", blink, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(previous, game.state.cards[source.object_id].logical_object_id)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        self.assertFalse(self.foods(game))
        self.replay(game, load=True)

    def test_actual_goose_attack_food_payment_and_decline_replay_with_private_draw(self):
        for take in (False, True):
            with self.subTest(take=take):
                game = self.session(107300530 + take)
                source = self.add(game.engine, "The Goose Mother")
                source.counters["+1/+1"] = 2
                source.acquired_control_turn_count = -1
                food = self.add(game.engine, "Gingerbrute", seat="B", ref="entry-food-borrowed")
                wrong = self.add(game.engine, "Gingerbrute", seat="D", ref="entry-food-foreign")
                game.engine.change_control(food.object_id, "A", reason="Seed borrowed Food")
                surprise = self.add(game.engine, "Mountain", zone="library", ref="entry-food-private-top")
                self.prepare(game)
                self.checkpoint(game)
                self.attack(game, [(source, "C")])
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                game = self.replay(game, load=True)
                command = {"action_id": "choose", "cards": [food.ref] if take else []}
                self.rejected(game, "B", command)
                self.rejected(game, "A", {**command, "cards": [wrong.ref]})
                self.accepted(game, "A", command)
                if take:
                    self.assertIn(food.object_id, game.state.players["B"].zones["graveyard"])
                    self.assertIn(surprise.object_id, game.state.players["A"].zones["hand"])
                else:
                    self.assertEqual("battlefield", game.state.cards[food.object_id].zone)
                    self.assertEqual("library", game.state.cards[surprise.object_id].zone)
                self.assertNotIn(surprise.ref, json.dumps(game.packet("spectator", full=True)))
                self.replay(game, load=True)
