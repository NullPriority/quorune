from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_vanishing_rules as upkeep_witness
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.drawing.step_exemption import is_first_draw_in_own_draw_step
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import draw_replacements
from scripts.build_test_database import build_fixture_database


class DrawStepExemptMultiplierTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "draw-step-exempt.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/draw-step-exempt-multiplier-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("First actual draw", [DeckEntry(commander, 1, "commander"),
                                                       DeckEntry("Plains", 30)], [commander])

    def draws(self, game, seat="A", *, during_step=False):
        tracker = "cards_drawn_in_draw_step_by_turn" if during_step else "cards_drawn_by_turn"
        return game.state.players[seat].stats.get(tracker, {}).get(str(game.state.turn_sequence), 0)

    def upkeep(self, game):
        upkeep_witness.VanishingRuntimeTests.upkeep(self, game, seat="A")

    def followup(self, game, actor, spell):
        self.cast_for(game, actor, spell)
        self.assertIsNone(self.resolve(game))

    def reach_draw_step(self, game):
        self.until(game, lambda current: (current.state.phase, current.state.step) == ("beginning", "draw")
            and current.state.pending_decision is not None and current.state.pending_decision.kind == "priority")

    def assert_first_step_draw(self, game):
        self.reach_draw_step(game)
        self.assertEqual(1, self.draws(game, during_step=True), "the first actual draw in this step is exempt")

    def test_actual_both_originals_exempt_first_draw_only_in_their_own_draw_step(self):
        for offset, name in enumerate(("Teferi's Ageless Insight", "Alhammarret's Archive")):
            with self.subTest(name=name):
                game = self.session(121060801 + offset)
                source = self.add(game.engine, name)
                before_step = self.add(game.engine, "Reach Through Mists", zone="hand", ref="before-first-step")
                after_first = self.add(game.engine, "Reach Through Mists", zone="hand", ref="after-first-step")
                for index in range(2):
                    self.add(game.engine, "Island", ref=f"step-draw-island-{index}")
                for card in (source, before_step, after_first):
                    self.assert_closed(game, card)
                self.prepare(game)
                self.upkeep(game)
                self.checkpoint(game)
                start = self.draws(game)
                self.followup(game, "A", before_step)
                self.assertEqual(start + 2, self.draws(game))
                self.assertEqual(0, self.draws(game, during_step=True))
                self.assert_first_step_draw(game)
                self.followup(game, "A", after_first)
                self.assertEqual(3, self.draws(game, during_step=True))
                self.assertEqual(start + 5, self.draws(game))
                self.replay(game, load=True)

    def test_actual_dredge_does_not_consume_the_first_completed_draw_exemption(self):
        game = self.session(121060803)
        source = self.add(game.engine, "Teferi's Ageless Insight")
        dredge = self.add(game.engine, "Darkblast", zone="graveyard")
        first = self.add(game.engine, "Reach Through Mists", zone="hand", ref="first-after-dredge")
        second = self.add(game.engine, "Reach Through Mists", zone="hand", ref="later-after-dredge")
        for card in (source, dredge, first, second):
            self.assert_closed(game, card)
        for index in range(2):
            self.add(game.engine, "Island", ref=f"post-dredge-island-{index}")
        self.prepare(game)
        self.upkeep(game)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "draw.replacement")
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(["draw", dredge.ref], [row["id"] for row in options])
        result = game.act("pilot:A", {"action_id": "choose", "choice": dredge.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", game.state.cards[dredge.object_id].zone)
        self.assertEqual(0, self.draws(game, during_step=True))
        self.followup(game, "A", first)
        self.assertEqual(1, self.draws(game, during_step=True))
        self.followup(game, "A", second)
        self.assertEqual(3, self.draws(game, during_step=True))
        self.replay(game, load=True)

    def test_actual_changed_controller_supplies_the_replacement_for_the_current_player(self):
        game = self.session(121060804)
        source = self.add(game.engine, "Teferi's Ageless Insight")
        steal = self.add(game.engine, "Word of Seizing", seat="B", zone="hand")
        draw_a = self.add(game.engine, "Reach Through Mists", zone="hand", ref="old-draw-controller")
        draw_b = self.add(game.engine, "Reach Through Mists", seat="B", zone="hand", ref="new-draw-controller")
        self.add(game.engine, "Island", seat="B", ref="new-controller-island")
        for card in (source, steal, draw_a, draw_b):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 1})
        game.state.players["B"].mana_pool.update({"C": 3, "R": 2})
        self.checkpoint(game)
        before_a, before_b = self.draws(game), self.draws(game, "B")
        self.priority(game, "B")
        offered = self.action(game, "B", steal)
        result = game.act("pilot:B", {"action_id": offered["id"], "targets": [source.ref],
                                      "pay": "manual", "payment": {"C": 3, "R": 2}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        self.followup(game, "A", draw_a)
        self.followup(game, "B", draw_b)
        self.assertEqual(before_a + 1, self.draws(game))
        self.assertEqual(before_b + 2, self.draws(game, "B"))
        self.replay(game, load=True)

    def test_actual_source_whose_abilities_are_overwritten_supplies_no_draw_replacement(self):
        game = self.session(121060805)
        source = self.add(game.engine, "Alhammarret's Archive")
        song = self.add(game.engine, "Song of the Dryads", seat="B")
        spell = self.add(game.engine, "Reach Through Mists", zone="hand")
        for card in (source, song, spell):
            self.assert_closed(game, card)
        attach_objects(game.state.cards, song, source, source_timestamp=song.zone_timestamp, players=game.state.players)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        before = self.draws(game)
        self.followup(game, "A", spell)
        self.assertEqual(before + 1, self.draws(game))
        self.replay(game, load=True)

    def test_tracker_rejects_bool_and_does_not_count_draws_outside_the_step(self):
        player = SimpleNamespace(stats={"cards_drawn_by_turn": {"9": 4}})
        state = SimpleNamespace(active_player="A", phase="beginning", step="draw", turn_sequence=9,
                                players={"A": player})
        self.assertTrue(is_first_draw_in_own_draw_step(state, "A"))
        player.stats["cards_drawn_in_draw_step_by_turn"] = {"9": True}
        with self.assertRaises(ValueError):
            is_first_draw_in_own_draw_step(state, "A")

    def test_mutant_replaces_the_exempt_draw_and_is_detected_by_native_actions(self):
        game = self.session(121060806)
        source = self.add(game.engine, "Teferi's Ageless Insight")
        self.assert_closed(game, source)
        self.prepare(game)
        self.upkeep(game)
        self.checkpoint(game)
        with patch.object(draw_replacements, "is_first_draw_in_own_draw_step", return_value=False):
            with self.assertRaisesRegex(AssertionError, "the first actual draw in this step is exempt"):
                self.assert_first_step_draw(game)
