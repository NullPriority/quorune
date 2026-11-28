"""Paper name-sticker setup and normal Goblin trigger witnesses."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import PropertyMock, patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.name_stickers import ACCESS, NameSticker, access, choices, placements, registration
from quorune.rules.name_sticker_sheets import NAME_STICKER_SHEETS
from scripts.build_test_database import build_fixture_database

SOURCE = "_____ Goblin"
MYR_SLOT = "43f5ce56-6ad1-4a42-b1d7-26f1c7933693:2"
SIX_SLOT = "065cdf8d-6874-4ab6-a08e-79bd88b245bd:1"
HOT_DOG_SLOT = "481c3bfe-2293-447d-aa03-3ea6ed9321aa:1"


class SourceNameStickerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixture_root = ROOT / "tests/fixtures"
        path = Path(cls.temporary.name) / "name-sticker.sqlite3"
        build_fixture_database([
            fixture_root / "source-name-sticker-cards.json",
            fixture_root / "committed-damage-dealer-optional-draw-cards.json",
            fixture_root / "targeted-opponent-library-top-exile-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = cls.make_deck()

    @staticmethod
    def make_deck(*, sheets=True):
        commander = "Isamaru, Hound of Konda"
        entries = [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)]
        if sheets:
            entries += [DeckEntry(name, 1, "stickers") for name, _words in NAME_STICKER_SHEETS.values()]
        return DeckDefinition("Captured constructed name stickers", entries, [commander])

    def test_registration_rejects_incomplete_and_duplicate_sheets_and_samples_three(self):
        scene = self.session(74234101)
        player = scene.state.players["A"]
        access_view = access(player)
        self.assertEqual((10, 3), (len(access_view["registered"]), len(access_view["chosen"])))
        self.assertEqual(3, len(set(access_view["chosen"])))
        self.assertEqual(9, len(choices(scene.state, "A")))
        self.assertEqual(31, self.deck.total_cards())
        self.assertTrue(any(e.code == "sticker.sheets" and e.actor == "A" for e in scene.state.events))
        for entries in (self.deck.entries[:-1], self.deck.entries + [self.deck.entries[-1]]):
            with self.assertRaises(GameRuleError):
                registration(self.db, DeckDefinition(self.deck.name, entries, self.deck.commanders), seed=1, seat="A")
        self.assertIsNone(registration(self.db, self.make_deck(sheets=False), seed=1, seat="A"))
        original = self.deck
        self.deck = DeckDefinition(original.name, [*original.entries, DeckEntry("Ancestral Hot Dog Minotaur", 1)], original.commanders)
        try:
            with self.assertRaisesRegex(GameRuleError, "register separately"):
                self.session(74234105)
        finally:
            self.deck = original
        self.assertEqual((1, 6), (NameSticker.parse(MYR_SLOT).red_mana, NameSticker.parse(SIX_SLOT).red_mana))
        self.assertEqual("Hot Dog", NameSticker.parse(HOT_DOG_SLOT).text)
        with patch.object(NameSticker, "red_mana", new_callable=PropertyMock, return_value=0):
            with self.assertRaises(AssertionError):
                self.assertEqual((1, 6), (NameSticker.parse(MYR_SLOT).red_mana, NameSticker.parse(SIX_SLOT).red_mana))

    def game_with_slot(self, slot, seed):
        for nonce in range(40):
            selected = registration(self.db, self.deck, seed=seed + nonce, seat="A")["chosen"]
            if slot.split(":")[0] in selected:
                return self.session(seed + nonce)
        self.fail("Fixture had no game seed containing the required independent slot")

    def cast_source(self, game):
        source = self.add(game.engine, SOURCE, zone="hand", ref="sticker-goblin")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "R": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        return source

    def choose(self, game, seat, value, *, succeeds=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "choice": value})
        self.assertEqual(succeeds, result.ok, result.summary)
        if not succeeds:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_y_only_slot_is_used_once_and_adds_exact_red_with_replay(self):
        game = self.game_with_slot(MYR_SLOT, 74234121)
        source = self.cast_source(game)
        pending = self.resolve(game)
        self.assertEqual(("A",), tuple(pending.actors))
        self.choose(game, "B", MYR_SLOT, succeeds=False)
        self.choose(game, "A", "invalid-sheet:0", succeeds=False)
        game = self.replay(game, load=True)
        self.choose(game, "A", MYR_SLOT)
        self.assertEqual(("A",), tuple(game.state.pending_decision.actors))
        before = game.state.players["A"].mana_pool.get("R", 0)
        self.choose(game, "A", "1")
        card = game.state.cards[source.object_id]
        self.assertEqual("Goblin Myr", game.engine._effective_card_data(card)["name"])
        self.assertEqual(before + 1, game.state.players["A"].mana_pool["R"])
        self.assertNotIn(MYR_SLOT, {sticker.key for sticker in choices(game.state, "A")})
        row, = placements(card)
        self.assertEqual((MYR_SLOT, 1), (row.sticker.key, row.position))
        self.replay(game, load=True)

    def test_actual_restored_name_sticker_distinguishes_picker_and_position_controller(self):
        game = self.game_with_slot(SIX_SLOT, 74234141)
        engine = game.engine
        source = self.add(engine, SOURCE, zone="hand", ref="sticker-goblin")
        control = self.add(engine, "Word of Seizing", seat="B", zone="hand")
        for index in range(5):
            self.add(engine, "Mountain", seat="B", ref=f"sticker-mana-{index}")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "R": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.cast_for(game, "B", control, [source.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        pending = self.resolve(game)
        self.assertEqual(("A",), tuple(pending.actors))
        self.choose(game, "A", SIX_SLOT)
        self.assertEqual(("B",), tuple(game.state.pending_decision.actors))
        self.choose(game, "A", "0", succeeds=False)
        game = self.replay(game, load=True)
        before = {seat: game.state.players[seat].mana_pool.get("R", 0) for seat in "AB"}
        self.choose(game, "B", "0")
        self.assertEqual((before["A"] + 6, before["B"]),
                         tuple(game.state.players[seat].mana_pool.get("R", 0) for seat in "AB"))
        current = game.state.cards[source.object_id]
        self.assertEqual("Delusionary Goblin", game.engine._effective_card_data(current)["name"])
        self.replay(game, load=True)

    def test_decline_no_sheet_and_public_to_hidden_continuity(self):
        game = self.game_with_slot(HOT_DOG_SLOT, 74234161)
        source = self.cast_source(game)
        self.resolve(game)
        before = game.state.players["A"].mana_pool.get("R", 0)
        self.choose(game, "A", "decline")
        self.assertEqual(before, game.state.players["A"].mana_pool.get("R", 0))
        self.assertFalse(placements(game.state.cards[source.object_id]))
        self.replay(game, load=True)

        game = self.game_with_slot(HOT_DOG_SLOT, 74234171)
        source = self.cast_source(game)
        self.resolve(game)
        self.choose(game, "A", HOT_DOG_SLOT)
        self.choose(game, "A", "0")
        current = game.state.cards[source.object_id]
        original, = placements(current)
        self.assertEqual("Hot Dog Goblin", game.engine._effective_card_data(current)["name"])
        game.engine.move_card(current.object_id, "exile", reason="Explicit physical zone witness")
        self.assertEqual((original,), placements(game.state.cards[source.object_id]))
        game.engine.move_card(current.object_id, "hand", reason="Explicit hidden zone witness")
        self.assertFalse(placements(game.state.cards[source.object_id]))
        self.assertIn(HOT_DOG_SLOT, {sticker.key for sticker in choices(game.state, "A")})

        previous = self.deck
        self.deck = self.make_deck(sheets=False)
        try:
            game = self.session(74234181)
            self.assertNotIn(ACCESS, game.state.players["A"].stats)
            source = self.cast_source(game)
            before = game.state.players["A"].mana_pool.get("R", 0)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(before, game.state.players["A"].mana_pool.get("R", 0))
            self.assertFalse(placements(game.state.cards[source.object_id]))
            self.replay(game, load=True)
        finally:
            self.deck = previous
