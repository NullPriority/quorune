"""Native source-entry, library-ordering, and outcome witnesses."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT, packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_choices import variable_library_selection
from scripts.build_test_database import build_fixture_database


class DevotionLibraryWinNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "devotion-library-win.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/public-zone-quantity-cards.json",
            ROOT / "tests/fixtures/match-outcome-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Devotion library witness", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def start(self, seed, size, *, blue_sources=0, prevent=False, remove=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        engine = game.engine
        source = self.add(engine, "Thassa's Oracle", zone="hand", ref="devotion-source")
        self.assert_closed(game, source)
        library = game.state.players["A"].zones["library"]
        for object_id in tuple(library[:len(library) - size]):
            engine.move_card(object_id, "exile", log=False)
        for number in range(blue_sources):
            self.add(engine, "Crackling Drake", ref=f"devotion-blue-{number}")
        self.add(engine, "Crackling Drake", seat="B", ref="foreign-devotion-blue")
        if prevent:
            prohibition = self.add(engine, "Platinum Angel", seat="B")
            self.assert_closed(game, prohibition)
        removal = self.add(engine, "Lightning Bolt", seat="B", zone="hand") if remove else None
        if removal is not None:
            self.assert_closed(game, removal)
            game.state.players["B"].mana_pool["R"] = 1
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertEqual("triggered_ability", game.state.stack[-1].kind)
        self.assertEqual(source.object_id, game.state.stack[-1].source_object_id)
        if removal is not None:
            self.cast_for(game, "B", removal, [source.ref])
            self.resolve_top(game)
            self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        return game

    @staticmethod
    def library(game):
        return tuple(game.state.players["A"].zones["library"])

    @staticmethod
    def card_refs(game, ids):
        return tuple(game.state.cards[object_id].ref for object_id in ids)

    def pending_look(self, game):
        self.assertEqual(["pilot:A"], game.pending_principals())
        decision = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual("semantic.choice", decision["kind"])
        self.assertEqual("fixed_library_selection", decision["ctx"]["operation"])
        return decision

    def resolve_look(self, game):
        choice = self.resolve(game)
        self.assertIsNotNone(choice)
        return self.pending_look(game)

    def choose(self, game, refs):
        result = game.act("pilot:A", {"action_id": "choose", "cards": list(refs)})
        self.assertTrue(result.ok, result.summary)

    def assert_private(self, game, refs):
        for seat in "BCD":
            view = game.packet(f"pilot:{seat}", full=True)
            encoded = json.dumps(view)
            for ref in refs:
                self.assertNotIn(ref, encoded)

    def test_live_devotion_bounds_private_look_and_random_bottom(self):
        game = self.start(700500206, 8, blue_sources=2)
        original = self.library(game)
        previous_events = len(game.state.events)
        allowed = self.card_refs(game, reversed(original[-6:]))
        decision = self.resolve_look(game)
        self.assertEqual(allowed, tuple(row["id"] for row in decision["ctx"]["objects"]))
        self.assertEqual(original, self.library(game))
        self.assert_private(game, allowed)
        prior = packet_without_decision_capability(game.packet("pilot:A", full=True))["decision"]
        game = self.replay(game, load=True)
        self.assertEqual(prior, packet_without_decision_capability(game.packet("pilot:A", full=True))["decision"])
        baseline = authoritative_state_hash(game.state)
        untouched = self.card_refs(game, original[:2])
        for seat, fields in (
            ("B", {"cards": [allowed[0]]}),
            ("A", {"cards": [allowed[0], allowed[1]]}),
            ("A", {"cards": [untouched[0]]}),
            ("A", {"cards": {"top": [], "bottom": list(allowed)}}),
            ("A", {"cards": [], "look_count": 0}),
        ):
            with self.subTest(seat=seat, fields=fields):
                self.assertFalse(game.act(f"pilot:{seat}", {"action_id": "choose", **fields}).ok)
                self.assertEqual(baseline, authoritative_state_hash(game.state))
        self.choose(game, [allowed[2]])

        final = self.library(game)
        self.assertEqual(original[-3], final[-1])
        self.assertEqual(original[:2], final[-3:-1])
        self.assertEqual(set(original[-6:]) - {original[-3]}, set(final[:5]))
        self.assertFalse(game.state.game_over)
        self.assertFalse(any(event.code in ("library.shuffle", "player.scried")
                             for event in game.state.events[previous_events:]))
        self.assert_private(game, allowed)
        self.replay(game, load=True)

    def test_short_library_compares_after_placement_and_wins(self):
        game = self.start(700500201, 1)
        refs = self.card_refs(game, self.library(game))
        self.resolve_look(game)
        self.assertFalse(game.state.game_over)
        game = self.replay(game, load=True)
        self.choose(game, ())
        phases = [event.code for event in game.state.events if event.code in {"library.look", "library.selection", "game.win"}]
        self.assertEqual(["library.look", "library.selection", "game.win"], phases)
        self.assertTrue(game.state.game_over)
        self.assertEqual("A", game.state.winner)
        self.assert_private(game, refs)
        self.replay(game, load=True)

    def test_departed_source_leaves_zero_devotion_and_no_inspection(self):
        for size in (0, 1):
            with self.subTest(size=size):
                game = self.start(700500210 + size, size, remove=True)
                original = self.library(game)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(original, self.library(game))
                self.assertFalse(any(event.code in {"library.look", "library.selection"} for event in game.state.events))
                self.assertEqual("A" if size == 0 else None, game.state.winner)
                self.assertEqual(size == 0, game.state.game_over)
                self.replay(game, load=True)

    def test_win_prohibition_preserves_the_private_ordering(self):
        game = self.start(700500202, 2, prevent=True)
        original = self.library(game)
        refs = self.card_refs(game, original)
        self.resolve_look(game)
        self.choose(game, [refs[0]])
        self.assertEqual(tuple(reversed(original)), self.library(game))
        self.assertIsNone(game.state.winner)
        self.assertFalse(game.state.game_over)
        self.assertTrue(any(event.code == "library.selection" for event in game.state.events))
        self.assert_private(game, refs)
        self.replay(game, load=True)

    def test_omitting_the_resolved_comparison_is_detected(self):
        game = self.start(700500203, 1)
        self.resolve_look(game)
        original = variable_library_selection._devotion_steps

        def omit_comparison(effect, context, selection):
            return () if effect.get("stage") == "compare" else original(effect, context, selection)

        with patch.object(variable_library_selection, "_devotion_steps", side_effect=omit_comparison) as mutant:
            self.choose(game, ())
        mutant.assert_called_once()
        with self.assertRaisesRegex(AssertionError, "the comparison must produce a win"):
            self.assertEqual("A", game.state.winner, "the comparison must produce a win")
        self.assertTrue(any(event.code == "library.selection" for event in game.state.events))


if __name__ == "__main__":
    unittest.main()
