"""Individual empty-library draws may end a match or replace an impossible win."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.drawing.model import DrawError, DrawEventRequest, prepare_draw_event
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement import ReplacementClass, ReplacementEffect
from quorune.replacement.operations import WinInsteadOfEmptyDraw, operation_from_dict
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.draw_empty_library_win import (
    CAPABILITY, EmptyLibraryWinDrawReplacementHandler, empty_library_win_descriptor,
)
from scripts.build_test_database import build_fixture_database


CARD = "Laboratory Maniac"
PROFILE = "commander_review"
WIN_REPLACED = "card.draw.replaced.win_game"


class EmptyLibraryWinDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "empty-library-win-draw.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "match-outcome-cards.json",
            fixtures / "opponent-draw-cards.json",
            fixtures / "draw-exile-play-cards.json",
            fixtures / "draw-rules-cards.json",
            fixtures / "turn-outcome-protection-cards.json",
            fixtures / "empty-library-win-draw-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Empty-library draw win", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def source(self, game, *, seat="A", ref=None):
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, CARD, seat=seat, ref=ref)
        self.assert_closed(game, source)
        return source

    @staticmethod
    def shorten_library(game, seat, *, keep=0):
        library = tuple(game.state.players[seat].zones["library"])
        remove = library[:-keep] if keep else library
        for object_id in remove:
            game.engine.move_card(object_id, "graveyard", log=False)

    @staticmethod
    def events(game, offset, code):
        return [event.actor for event in game.state.events if event.event_id > offset and event.code == code]

    @staticmethod
    def outcome(game):
        return game.state.game_over, game.state.winner

    def test_actual_apnap_second_draw_wins_for_current_controller_and_stops_all_later_draws(self):
        game = self.session(12106011)
        source = self.source(game, seat="C", ref="borrowed-empty-draw-source")
        self.source(game, seat="B", ref="nonactive-empty-draw-source")
        game.engine.change_control(source.object_id, "A", reason="Borrowed draw replacement fixture")
        spell = self.add(game.engine, "Vision Skeins", seat="B", zone="hand")
        self.shorten_library(game, "A", keep=1)
        self.shorten_library(game, "B")
        last, = game.state.players["A"].zones["library"]
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 1, "U": 1})
        self.checkpoint(game)
        before = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "BCD"}
        offset = game.state.event_sequence

        self.cast(game, "B", spell)
        self.until(game, lambda g: g.state.game_over)
        self.assertEqual((True, "A"), self.outcome(game))
        self.assertFalse(game.state.players["A"].attempted_empty_draw)
        self.assertEqual({seat: tuple(game.state.players[seat].zones["library"]) for seat in "BCD"}, before)
        self.assertEqual(["A"], self.events(game, offset, "card.draw"))
        self.assertEqual(["A"], self.events(game, offset, WIN_REPLACED))
        self.assertEqual([], self.events(game, offset, "card.draw.empty"))
        self.assertEqual("hand", game.state.cards[last].zone)
        hidden = game.state.cards[last].ref
        self.assertIn(hidden, json.dumps(game.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(hidden, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_prohibited_win_remains_replaced_and_other_players_complete_draws(self):
        game = self.session(12106012)
        self.source(game)
        spell = self.add(game.engine, "Vision Skeins", zone="hand")
        grace = self.add(game.engine, "Angel's Grace", seat="B", zone="hand")
        self.assert_closed(game, grace)
        self.shorten_library(game, "A")
        self.prepare(game, mana={"C": 1, "U": 1})
        game.state.players["B"].mana_pool.update({"W": 1})
        self.checkpoint(game)
        offset = game.state.event_sequence
        self.cast(game, "A", spell)
        self.cast(game, "B", grace)
        self.resolve_top(game)
        game = self.replay(game, load=True)

        self.assertIsNone(self.resolve(game))
        self.assertEqual((False, None), self.outcome(game))
        self.assertEqual(["A", "A"], self.events(game, offset, WIN_REPLACED))
        self.assertEqual(["B", "B", "C", "C", "D", "D"], self.events(game, offset, "card.draw"))
        self.assertEqual([], self.events(game, offset, "card.draw.empty"))
        self.assertTrue(game.state.players["A"].in_game)
        self.assertFalse(game.state.players["A"].attempted_empty_draw)
        self.replay(game, load=True)

    def test_competing_mandatory_replacements_resume_the_same_choice_and_terminal_path(self):
        for win in (False, True):
            with self.subTest(win=win):
                game = self.session(12106020 + win)
                win_source = self.source(game)
                exile_source = self.add(game.engine, "Eruth, Tormented Prophet")
                draw = self.add(game.engine, "Divination", zone="hand")
                self.assert_closed(game, exile_source)
                self.shorten_library(game, "A")
                self.prepare(game, mana={"C": 2, "U": 1})
                self.checkpoint(game)
                offset = game.state.event_sequence
                self.cast(game, "A", draw)
                self.assertEqual("draw.replacement", self.resolve(game).kind)
                game = self.replay(game, load=True)
                packet = game.packet("pilot:A", full=True)["decision"]
                options = packet["ctx"]["options"]
                by_source = {option["label"].split(":", 1)[0]: option["id"] for option in options}
                self.assertEqual({win_source.ref, exile_source.ref}, set(by_source))
                self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                requested = by_source[(win_source if win else exile_source).ref]
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", {"action_id": "choose", "choice": requested}).ok)
                self.assertFalse(game.act("pilot:A", {"action_id": "choose", "choice": "draw"}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                result = game.act("pilot:A", {"action_id": "choose", "choice": requested})
                self.assertTrue(result.ok, result.summary)
                if not win:
                    repeat = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                    choice, = (row["id"] for row in repeat if row["label"].startswith(exile_source.ref + ":"))
                    self.assertTrue(game.act("pilot:A", {"action_id": "choose", "choice": choice}).ok)
                    self.assertIsNone(self.resolve(game))
                self.assertEqual((win, "A" if win else None), self.outcome(game))
                self.assertEqual(["A"] if win else [], self.events(game, offset, WIN_REPLACED))
                self.assertEqual([], self.events(game, offset, "card.draw.empty"))
                self.assertFalse(game.state.players["A"].attempted_empty_draw)
                self.replay(game, load=True)

    def test_draw_prohibition_does_not_offer_or_commit_the_empty_library_replacement(self):
        game = self.session(12106013)
        self.source(game)
        limiter = self.add(game.engine, "Spirit of the Labyrinth", seat="B")
        self.assert_closed(game, limiter)
        draw = self.add(game.engine, "Divination", zone="hand")
        self.shorten_library(game, "A", keep=1)
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        offset = game.state.event_sequence
        self.cast(game, "A", draw)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((False, None), self.outcome(game))
        self.assertEqual(["A"], self.events(game, offset, "card.draw"))
        self.assertEqual(["A"], self.events(game, offset, "card.draw.prohibited"))
        self.assertEqual([], self.events(game, offset, WIN_REPLACED))
        self.replay(game, load=True)

    def test_normal_turn_draw_uses_the_same_empty_library_win(self):
        game = self.session(12106014)
        self.source(game, seat="B")
        self.shorten_library(game, "B")
        self.prepare(game)
        self.checkpoint(game)
        offset = game.state.event_sequence
        self.until(game, lambda g: g.state.game_over)
        self.assertEqual((True, "B"), self.outcome(game))
        self.assertEqual(("B", "draw"), (game.state.active_player, game.state.step))
        self.assertEqual(["B"], self.events(game, offset, WIN_REPLACED))
        self.assertEqual([], self.events(game, offset, "card.draw.empty"))
        self.replay(game, load=True)

    def test_complete_binding_withdrawal_codec_and_grammar_fail_closed(self):
        card = self.db.lookup(CARD, fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

        ir = compile_card(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(("draw", "battlefield"), (node.event, node.active_zone))
        self.assertEqual((0, len(card.oracle_text)), (node.span.start, node.span.end))
        self.assertEqual((empty_library_win_descriptor(),), tuple(node.handlers))
        self.assertIn(CAPABILITY, node.capability_dependencies)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertTrue(binding["blockers"])
        for old, new in (
            ("you would", "an opponent would"), ("has no cards", "has one card"),
            ("you win", "you may win"), ("game instead.", "game instead, then draw a card."),
        ):
            self.assertNotEqual("exact", compile_card(replace(card, oracle_text=card.oracle_text.replace(old, new))).status)
        with patch("quorune.compiler.runtime_templates.static_draw_result_handler", return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)

        handler = EmptyLibraryWinDrawReplacementHandler()
        descriptor = empty_library_win_descriptor()
        with self.assertRaises(SemanticNodeError):
            handler.validate({**descriptor, "condition": {**descriptor["condition"], "library_size": False}})
        operation = WinInsteadOfEmptyDraw()
        self.assertEqual(operation, operation_from_dict(operation.to_dict()))
        with self.assertRaises(ValueError):
            operation_from_dict({**operation.to_dict(), "player": "B"})
        unguarded = ReplacementEffect(
            effect_id="malformed-test", source_id="test", event_kind="draw",
            replacement_class=ReplacementClass.OTHER, conditions={}, operations=(operation,),
        )
        with self.assertRaises(DrawError):
            prepare_draw_event(DrawEventRequest("nonempty", "A", 1, "Test", True),
                               apnap_order=("A",), effects=(unguarded,))
