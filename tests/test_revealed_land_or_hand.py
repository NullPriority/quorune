"""Resolve the original's single public reveal and mandatory destination."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.one_revealed_card_placement import (
    OneRevealedCardPlacement, REVEALED_LAND_OR_HAND_CAPABILITY, REVEALED_LAND_OR_HAND_VERSION,
)
from scripts.build_test_database import build_fixture_database


CARD = "Coiling Oracle"


class RevealedLandOrHandTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        control = json.loads((fixtures / "external-entry-tapped-cards.json").read_bytes())
        maze, = (row for row in control["cards"] if row["name"] == "Root Maze")
        control["cards"] = [maze]
        control["rulings"] = [row for row in control.get("rulings", ()) if row["oracle_id"] == maze["oracle_id"]]
        maze_fixture = folder / "exact-root-maze-control.json"
        maze_fixture.write_text(json.dumps(control), encoding="utf-8", newline="\n")
        database = folder / "revealed-land-or-hand.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "coiling-oracle-placement-cards.json",
            maze_fixture,
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Revealed land or hand", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_original_exact_version_and_removed_authority_fail_closed(self):
        def compile_with(registry):
            return compile_oracle_card(self.db.lookup(CARD), capability_registry=registry,
                                       capability_profile="commander_review")

        normal = compile_with(self.registry)
        self.assertEqual("exact", normal.status, normal.material_residuals)
        node, = normal.faces[0].nodes
        self.assertIn(REVEALED_LAND_OR_HAND_CAPABILITY, node.capability_dependencies)
        spec = OneRevealedCardPlacement("land", False, "hand")
        effect, = node.effects
        self.assertEqual(REVEALED_LAND_OR_HAND_VERSION, effect["schema_version"])
        self.assertEqual(spec, OneRevealedCardPlacement.from_effect(effect))
        for malformed in (
            {**effect, "schema_version": 1},
            {**effect, "required_type": "creature"},
            {**effect, "entry_optional": True},
        ):
            with self.assertRaises(ValueError):
                OneRevealedCardPlacement.from_effect(malformed)
        with patch("quorune.compiler.revealed_library_top_templates.one_revealed_card_placement_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_with(self.registry).status)
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(entry for entry in raw["capabilities"] if entry["id"] == REVEALED_LAND_OR_HAND_CAPABILITY)
        row.update(status="blocked", blockers=["Withdraw the exact land-or-hand placement"])
        unresolved = compile_with(CapabilityRegistry(raw))
        self.assertNotEqual("exact", unresolved.status)
        self.assertTrue(any(REVEALED_LAND_OR_HAND_CAPABILITY in blocker
                            for residual in unresolved.material_residuals for blocker in residual.blockers))

    def test_original_places_land_without_a_land_play_and_honors_entry_replacement(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                game = self.session(7012041 + replacement)
                game.state.config.semantic_policy = "trusted_only"
                oracle = self.add(game.engine, CARD, zone="hand")
                hidden = self.add(game.engine, "Generic Bound Body", zone="library", ref="coiling-unrevealed")
                top = self.add(game.engine, "Generic Bound Plains", zone="library", ref="coiling-land")
                if replacement:
                    maze = self.add(game.engine, "Root Maze", seat="B")
                    self.assert_closed(game, maze)
                self.assert_closed(game, oracle)
                self.prepare(game, mana={"G": 1, "U": 1})
                game.state.players["A"].land_plays_remaining = 0
                self.checkpoint(game)
                self.cast(game, "A", oracle)
                self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                arrived = game.state.cards[top.object_id]
                self.assertEqual(("battlefield", "A", replacement), (arrived.zone, arrived.controller, arrived.tapped))
                self.assertEqual(0, game.state.players["A"].land_plays_remaining)
                reveals = [event.details["card"] for event in game.state.events if event.code == "library.top.reveal"]
                self.assertEqual([top.printed_name], reveals)
                self.assertEqual(hidden.object_id, game.state.players["A"].zones["library"][-1])
                self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:B", full=True)))
                self.replay(game, load=True)

    def test_original_nonland_goes_to_public_known_hand_without_draw_and_empty_is_safe(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                game = self.session(7012051 + empty)
                game.state.config.semantic_policy = "trusted_only"
                oracle = self.add(game.engine, CARD, zone="hand")
                if empty:
                    for object_id in tuple(game.state.players["A"].zones["library"]):
                        game.engine.move_card(object_id, "exile", log=False, semantic_events=False)
                else:
                    hidden = self.add(game.engine, "Generic Bound Plains", zone="library", ref="coiling-private-next")
                    top = self.add(game.engine, "Generic Bound Body", zone="library", ref="coiling-known-hand")
                self.prepare(game, mana={"G": 1, "U": 1})
                self.checkpoint(game)
                draws = len(game.engine._current_turn_history("card_drawn"))
                self.cast(game, "A", oracle)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(draws, len(game.engine._current_turn_history("card_drawn")))
                self.assertTrue(game.state.players["A"].in_game)
                reveals = [event.details["card"] for event in game.state.events if event.code == "library.top.reveal"]
                self.assertEqual([] if empty else [top.printed_name], reveals)
                if not empty:
                    current = game.state.cards[top.object_id]
                    self.assertEqual("hand", current.zone)
                    self.assertEqual(set(game.engine.seats), set(current.known_to))
                    self.assertEqual(set(game.engine.seats), set(current.revealed_to))
                    self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:B", full=True)))
                self.replay(game, load=True)
