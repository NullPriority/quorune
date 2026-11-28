"""A fixed Addendum suffix taps the original creature and prohibits one untap."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.cast_origin_replacement import MAIN_PHASE_CAST_UNTAP_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FixedMainPhaseTargetUntapTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-main-phase-target-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-main-phase-target-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Exact main-phase target untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_both_base_effects_and_exactly_one_next_untap(self):
        admission, semantics = register_card(self.db, self.registry, "Code of Constraint")
        self.assertTrue(admission["strict_capability_ready"], admission["blockers"])
        record = self.db.lookup("Code of Constraint", fuzzy=False)
        spell, = semantics.programs_for_oracle(record.oracle_id)
        current, = spell.effects
        self.assertEqual(MAIN_PHASE_CAST_UNTAP_VERSION, current["schema_version"])
        self.assertEqual(current["otherwise_effects"], current["effects"][:2])
        self.assertEqual(["modify_stats_until_end_of_turn", "draw", "tap", "prohibit_next_untap"],
                         [child["op"] for child in current["effects"]])
        switched = {**current["effects"][-1], "card": "$target.1"}
        forged = {**current, "effects": [*current["effects"][:-1], switched]}
        self.assertFalse(bind_semantic_program_runtime(
            replace(spell, effects=[forged]), capability_registry=self.registry, profile="commander_review",
        )["strict"])
        for text in (record.oracle_text.replace("Draw a card", "Draw two cards"),
                     record.oracle_text.replace("next untap step", "next two untap steps")):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(record, oracle_text=text), capability_registry=self.registry,
                capability_profile="commander_review",
            ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def hand_count(self, game, seat="A"):
        return len(game.state.players[seat].zones["hand"])

    def test_actual_main_phase_taps_or_restrains_an_already_tapped_creature_for_one_untap(self):
        for was_tapped in (False, True):
            with self.subTest(was_tapped=was_tapped):
                game = self.playing(60129001 + was_tapped)
                source = self.add(game.engine, "Code of Constraint", zone="hand")
                target = self.add(game.engine, "Grizzly Bears", seat="B")
                target.tapped = was_tapped
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 2, "U": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", source, [target.ref])
                before = self.hand_count(game)
                start = len(game.state.events)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(before + 1, self.hand_count(game))
                self.assertEqual(-2, game.engine._numeric_stat(target.object_id, "power"))
                self.assertTrue(self.current(game, target).tapped)
                self.assertIs(True, self.current(game, target).annotations[NEXT_UNTAP_PROHIBITION_ANNOTATION])
                codes = [event.code for event in game.state.events[start:]]
                self.assertLess(codes.index("card.draw"), codes.index("permanent.next_untap.prohibited"))
                game = self.replay(game, load=True)
                self.until(game, lambda state: state.state.active_player == "B" and state.state.step == "upkeep")
                self.assertTrue(self.current(game, target).tapped)
                self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, self.current(game, target).annotations)
                self.replay(game, load=True)

    def test_actual_opponent_main_phase_keeps_debuff_and_draw_without_tap_or_restriction(self):
        game = self.session(60129003)
        source = self.add(game.engine, "Code of Constraint", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        self.assert_closed(game, source)
        self.prepare(game, active="B", mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [target.ref])
        prior = self.hand_count(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior + 1, self.hand_count(game))
        self.assertEqual(-2, game.engine._numeric_stat(target.object_id, "power"))
        self.assertFalse(self.current(game, target).tapped)
        self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, self.current(game, target).annotations)
        self.replay(game, load=True)

    def test_actual_returned_creature_has_a_new_identity_so_neither_branch_resolves(self):
        game = self.session(60129004)
        source = self.add(game.engine, "Code of Constraint", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand", seat="B")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        for card in (source, blink):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 1})
        game.state.players["B"].mana_pool["W"] = 1
        self.checkpoint(game)
        original = target.logical_object_id
        self.cast_for(game, "A", source, [target.ref])
        prior = self.hand_count(game)
        self.cast_for(game, "B", blink, [target.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, self.current(game, target).logical_object_id)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior, self.hand_count(game))
        self.assertEqual(2, game.engine._numeric_stat(target.object_id, "power"))
        self.assertFalse(self.current(game, target).tapped)
        self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, self.current(game, target).annotations)
        self.replay(game, load=True)
