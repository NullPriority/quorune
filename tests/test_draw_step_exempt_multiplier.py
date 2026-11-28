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
        build_fixture_database([ROOT / "tests/fixtures/draw-step-exempt-multiplier-cards.json", ROOT / "tests/fixtures/draw-rules-cards.json"], path)
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

    def _open_distinct_opponent_draw(self, game):
        source = self.add(game.engine, "Cut a Deal", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"W": 1, "C": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        return self.resolve(game)

    def test_actual_cut_a_deal_counts_an_expanded_draw_once_and_an_empty_draw_zero(self):
        import json

        game = self.session(121060841)
        insight = self.add(game.engine, "Teferi's Ageless Insight", seat="B")
        self.assert_closed(game, insight)
        for object_id in tuple(game.state.players["C"].zones["library"]):
            game.engine.move_card(object_id, "exile", reason="Prepare an empty opponent library")
        drawn = {seat: game.state.cards[game.state.players[seat].zones["library"][-1]]
                 for seat in "ABD"}
        before = {seat: self.draws(game, seat) for seat in "ABCD"}
        self.assertIsNone(self._open_distinct_opponent_draw(game))
        self.assertEqual((2, 2, 0, 1), tuple(self.draws(game, seat) - before[seat] for seat in "ABCD"))
        self.assertFalse(game.state.players["C"].in_game)
        for owner, card in drawn.items():
            self.assertEqual("hand", game.state.cards[card.object_id].zone)
            for seat in game.engine.active_seats:
                if seat != owner:
                    self.assertNotIn(card.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_cut_a_deal_preserves_private_replacement_resume_and_counts_other_opponents(self):
        from quorune.record import authoritative_state_hash

        game = self.session(121060842)
        dredge = self.add(game.engine, "Darkblast", seat="B", zone="graveyard")
        self.assert_closed(game, dredge)
        before = {seat: self.draws(game, seat) for seat in "ABCD"}
        decision = self._open_distinct_opponent_draw(game)
        self.assertEqual(("draw.replacement", ["B"]), (decision.kind, decision.actors))
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        command = {"action_id": "choose", "choice": dredge.ref}
        checkpoint = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(checkpoint, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        answer = game.act("pilot:B", command)
        self.assertTrue(answer.ok, answer.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 0, 1, 1), tuple(self.draws(game, seat) - before[seat] for seat in "ABCD"))
        self.assertEqual("hand", game.state.cards[dredge.object_id].zone)
        self.replay(game, load=True)

    def test_actual_cut_a_deal_does_not_reward_prohibited_draws(self):
        game = self.session(121060843)
        spirit = self.add(game.engine, "Spirit of the Labyrinth")
        self.assert_closed(game, spirit)
        for seat in "BCD":
            tracker = game.state.players[seat].stats.setdefault("cards_drawn_by_turn", {})
            tracker[str(game.state.turn_sequence)] = 1
        before = {seat: self.draws(game, seat) for seat in "ABCD"}
        self.assertIsNone(self._open_distinct_opponent_draw(game))
        self.assertEqual(before, {seat: self.draws(game, seat) for seat in "ABCD"})
        self.assertTrue(all(game.state.players[seat].in_game for seat in "ABCD"))
        self.replay(game, load=True)


class DistinctOpponentDrawCompilerTests(unittest.TestCase):
    def test_distinct_draw_compiler_requires_the_complete_two_stage_result(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.opponent_draw_result import opponent_draw_effects
        from test_qualified_zone_event_queries import query_record

        text = "Each opponent draws a card, then you draw a card for each opponent who drew a card this way."

        def compile_text(source):
            return compile_oracle_card(replace(query_record(source), type_line="Sorcery"),
                                       capability_registry=load_default_capability_registry())

        def witness():
            ir = compile_text(text)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node = ir.faces[0].nodes[0]
            self.assertEqual(opponent_draw_effects(), tuple(node.effects))
            self.assertIsNone(node.target_schema)

        witness()
        with patch("quorune.compiler.draw_templates.distinct_opponent_draw_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        for altered in (
            text.replace("each opponent who", "each card an opponent"),
            text.replace("Each opponent", "Target opponent"),
            text.replace("draws a card", "may draw a card", 1),
            text + " Scry 1.",
        ):
            self.assertNotEqual("exact", compile_text(altered).status)

    def test_forged_opponent_observer_or_single_phase_loses_runtime_trust(self):
        from quorune.card_programs.runtime_requirements import program_dynamic_runtime_requirements
        from quorune.rules.opponent_draw_result import (
            OPPONENT_DRAW_CAPABILITY, ObserveOpponentDraw, opponent_draw_effects,
        )
        from quorune.semantics import SemanticProgram

        opening, closing = opponent_draw_effects()
        forged = {
            "op": "draw_with_actions", "count": 1, "player": "B", "private": True,
            "post_draw_actions": [ObserveOpponentDraw("stack:forged", "A").to_dict()],
        }
        malformed = ((opening,), (closing, opening), (forged,), ({**opening, "player": "B"}, closing))
        for effects in malformed:
            with self.subTest(effects=effects):
                program = SemanticProgram(
                    key="fixture:distinct-opponent-draw", label="Distinct draw test", event="resolve",
                    active_zone="stack", effects=list(effects), capability_dependencies=(),
                )
                required, blockers = program_dynamic_runtime_requirements(program)
                self.assertNotIn(OPPONENT_DRAW_CAPABILITY, required)
                self.assertTrue(any("distinct_opponent_draw" in blocker for blocker in blockers), blockers)
