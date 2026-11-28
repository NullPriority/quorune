"""Original Chapter II permanent snapshot and each incarnation's controller-untap rule."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.affected_permanents import (
    TARGET_OPPONENT_NONLAND_UNTAP_CAPABILITY, TARGET_OPPONENT_NONLAND_UNTAP_SNAPSHOT_FIELD,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_next_player_untap_restrictions as untap_fixture


NAME = "Kiora Bests the Sea God"
PROFILE = "commander_review"


class KioraNonlandUntapTests(InheritedNativeFixture, unittest.TestCase):
    activate = untap_fixture.NextPlayerUntapRestrictionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "kiora.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "tapped-creature-player-untap-snapshot-cards.json",
            fixtures / "same-entrant-land-advantage-cards.json",
            fixtures / "kiora-sea-god-untap-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Kiora", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_chapter_two_binds_the_exact_announced_opponent_and_nonland_cohort(self):
        card = self.db.lookup(NAME, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        chapter, = (row for row in semantics.programs_for_oracle(card.oracle_id)
                    if TARGET_OPPONENT_NONLAND_UNTAP_CAPABILITY in row.capability_dependencies)
        self.assertEqual("saga.chapter.2", chapter.event)
        self.assertEqual("opponent", chapter.target_schema["player_relation"])
        row, = chapter.effects
        self.assertEqual(1, row[TARGET_OPPONENT_NONLAND_UNTAP_SNAPSHOT_FIELD])
        malformed = {**row, "set": {**row["set"], "target_controller": "$controller"}}
        for invalid in (replace(chapter,effects=[malformed]),
                        replace(chapter,target_schema={**chapter.target_schema,"player_relation":"any"}),
                        replace(chapter,event="saga.chapter.1")):
            self.assertFalse(bind_semantic_program_runtime(
                invalid, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("their controller's", "that player's"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)

    def card(self, game, original):
        return game.state.cards[original.object_id]

    def upkeep(self, game, seat):
        self.until(game, lambda current: current.state.active_player == seat and current.state.step == "upkeep")

    def test_actual_chapter_tracks_the_original_nonlands_across_control_and_source_departure(self):
        game = self.session(47839901)
        saga = self.add(game.engine, NAME, zone="hand")
        swap = self.add(game.engine, "Switcheroo", zone="hand")
        destroy = self.add(game.engine, "Naturalize", zone="hand", seat="B", ref="private-saga-removal")
        transferred = self.add(game.engine, "Grizzly Bears", seat="C", ref="original-transferred-bear")
        held = self.add(game.engine, "Llanowar Elves", seat="C", ref="original-tapped-elf")
        newcomer = self.add(game.engine, "Llanowar Elves", seat="B", ref="outside-cohort-elf")
        land = self.add(game.engine, "Forest", seat="C", ref="affected-seat-land")
        held.acquired_control_turn_count = newcomer.acquired_control_turn_count = -1
        for index in range(5):
            self.add(game.engine, "Island", ref=f"source-island-{index}")
        for index in range(2):
            self.add(game.engine, "Forest", seat="B", ref=f"destroyer-forest-{index}")
        for card in (saga, swap, destroy):
            self.assert_closed(game, card)
        needed = {card.object_id for card in (saga, swap, destroy)}
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                if object_id not in needed:
                    game.engine.move_card(object_id, "library", semantic_events=False, log=False)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active="A", mana={"U": 2, "C": 5})
        self.checkpoint(game)
        self.cast(game, "A", saga)
        self.settle(game)
        self.assertEqual(1, self.card(game, saga).counters["lore"])
        tokens = [game.engine._effective_card_data(card) for card in game.state.cards.values()
                  if card.is_token and card.zone == "battlefield" and card.controller == "A"]
        self.assertTrue(any("kraken" in game.engine._type_parts(data["type_line"])[1] for data in tokens),
                        [data["type_line"] for data in tokens])
        first_turn = game.state.turn_sequence
        for seat in ("B", "C", "D"):
            self.upkeep(game, seat)
        self.until(game, lambda current: current.state.active_player == "A"
                   and current.state.turn_sequence > first_turn
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "semantic.target")
        game = self.replay(game, load=True)
        self.assertNotIn(destroy.ref, str(game.packet("pilot:A", full=True)))
        self.target(game, "A", ["C"])
        self.activate(game, "B", newcomer)
        self.activate(game, "C", held)
        self.assertTrue(self.card(game, held).tapped)
        self.assertIsNone(self.resolve(game))
        marker = NEXT_UNTAP_PROHIBITION_ANNOTATION
        for original in (transferred, held):
            self.assertTrue(self.card(game, original).tapped)
            self.assertIs(True, self.card(game, original).annotations.get(marker))
        self.assertFalse(self.card(game, land).tapped)
        self.assertNotIn(marker, self.card(game, newcomer).annotations)
        self.cast(game, "A", swap, [transferred.ref, newcomer.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("B", "C"), (self.card(game, transferred).controller, self.card(game, newcomer).controller))
        self.cast(game, "B", destroy, [saga.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.card(game, saga).zone)
        self.upkeep(game, "B")
        self.assertTrue(self.card(game, transferred).tapped)
        self.assertNotIn(marker, self.card(game, transferred).annotations)
        self.assertIn(marker, self.card(game, held).annotations)
        self.upkeep(game, "C")
        self.assertTrue(self.card(game, held).tapped)
        self.assertNotIn(marker, self.card(game, held).annotations)
        self.assertFalse(self.card(game, newcomer).tapped)
        self.replay(game, load=True)

    def test_native_chapter_witness_detects_missing_controller_untap_restraint(self):
        with patch("quorune.public_tap_state_sets.prohibit_next_untap", return_value=None):
            with self.assertRaisesRegex(AssertionError, "True is not None"):
                self.test_actual_chapter_tracks_the_original_nonlands_across_control_and_source_departure()
