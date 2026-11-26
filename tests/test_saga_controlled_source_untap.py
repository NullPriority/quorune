"""Time of Ice binds its original lore triggers to the controlled-source restraint."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_tap_effect import SOURCE_CONTROL_UNTAP_FIELD
from quorune.source_untap_rule_model import ResolvedSourceUntapRestraint
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class SagaControlledSourceUntapTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "saga-controlled-source-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/saga-controlled-source-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saga controlled-source restraint", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_time_of_ice_produces_both_original_targeted_chapters_and_the_final_return(self):
        report, semantics = register_card(self.db, self.registry, "Time of Ice")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Time of Ice", fuzzy=False)
        chapters = {p.event: p for p in semantics.programs_for_oracle(record.oracle_id) if p.event.startswith("saga.chapter.")}
        self.assertEqual({"saga.chapter.1", "saga.chapter.2", "saga.chapter.3"}, set(chapters))
        for index in (1, 2):
            chapter = chapters[f"saga.chapter.{index}"]
            self.assertEqual("opponent", chapter.target_schema["controller_relation"])
            self.assertEqual(1, chapter.effects[0][SOURCE_CONTROL_UNTAP_FIELD])
        self.assertIsNone(chapters["saga.chapter.3"].target_schema)
        self.assertFalse(bind_semantic_program_runtime(
            replace(chapters["saga.chapter.1"], event="saga.chapter.3"),
            capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.saga_chapter_nodes.controlled_saga_untap_restraint_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def restraints(self, game):
        return [effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedSourceUntapRestraint)]

    def continue_to_next_own_turn(self, game):
        for seat in ("B", "C", "D", "A"):
            self.until(game, lambda value: value.state.active_player == seat and value.state.step == "upkeep")

    def test_actual_time_of_ice_separately_holds_two_creatures_and_returns_all_tapped_creatures(self):
        game = self.playing(61126401)
        saga = self.add(game.engine, "Time of Ice", zone="hand")
        first = self.add(game.engine, "Wind Drake", seat="B", ref="first-chapter-creature")
        second = self.add(game.engine, "Wind Drake", seat="C", ref="second-chapter-creature")
        unrelated = self.add(game.engine, "Grizzly Bears")
        land = self.add(game.engine, "Island")
        twiddle = self.add(game.engine, "Twiddle", zone="hand")
        for card in (saga, first, second, unrelated, land, twiddle):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", saga)
        self.target(game, "A", [first.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.restraints(game)))
        self.continue_to_next_own_turn(game)
        self.assertTrue(self.current(game, first).tapped)
        self.assertFalse(self.current(game, second).tapped)
        self.target(game, "A", [second.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(self.restraints(game)))
        game = self.replay(game, load=True)
        self.continue_to_next_own_turn(game)
        self.assertTrue(self.current(game, first).tapped)
        self.assertTrue(self.current(game, second).tapped)
        self.until(game, lambda value: self.current(value, saga).counters.get("lore", 0) == 3 and bool(value.state.stack))
        self.cast_for(game, "A", twiddle, [unrelated.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        applied = game.act("pilot:A", {"action_id": "choose", "choice": "tap"})
        self.assertTrue(applied.ok, applied.summary)
        self.assertIsNone(self.resolve(game))
        for recipient in (first, second, unrelated):
            with self.subTest(recipient=recipient.ref):
                self.assertEqual("hand", self.current(game, recipient).zone)
        self.assertEqual("graveyard", self.current(game, saga).zone)
        self.assertEqual([], self.restraints(game))
        self.replay(game, load=True)

    def test_actual_time_of_ice_pending_second_chapter_taps_after_original_saga_departs(self):
        game = self.playing(61126402)
        saga = self.add(game.engine, "Time of Ice", zone="hand")
        first = self.add(game.engine, "Wind Drake", seat="B", ref="held-before-disappearance")
        second = self.add(game.engine, "Grizzly Bears", seat="B", ref="tapped-after-disappearance")
        response = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        payment = [self.add(game.engine, "Plains", seat="B", ref=name) for name in ("disenchant-white", "disenchant-generic")]
        for card in (saga, first, second, response, *payment):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", saga)
        self.target(game, "A", [first.ref])
        self.assertIsNone(self.resolve(game))
        self.continue_to_next_own_turn(game)
        self.assertTrue(self.current(game, first).tapped)
        self.target(game, "A", [second.ref])
        self.cast_for(game, "B", response, [saga.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, saga).zone)
        self.assertTrue(self.current(game, second).tapped)
        self.assertEqual([], self.restraints(game))
        self.until(game, lambda value: value.state.active_player == "B" and value.state.step == "upkeep")
        self.assertFalse(self.current(game, first).tapped)
        self.assertFalse(self.current(game, second).tapped)
        self.replay(game, load=True)
