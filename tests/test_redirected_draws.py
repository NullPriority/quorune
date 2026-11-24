from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_vanishing_rules as upkeep_witness
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.declaration_rule_effects import continuous_journal_effect_from_dict
from quorune.draw_redirect_model import (
    DRAW_REDIRECT_CAPABILITY,
    ResolvedDrawRedirect,
    temporary_draw_redirect_runtime_dependencies,
)
from quorune.record import authoritative_state_hash
from quorune.replacement import CreateResultDraws
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class RedirectedDrawsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "redirected-draws.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/redirected-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Redirected draws", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def draws(self, game, seat, *, draw_step=False):
        field = "cards_drawn_in_draw_step_by_turn" if draw_step else "cards_drawn_by_turn"
        return game.state.players[seat].stats.get(field, {}).get(str(game.state.turn_sequence), 0)

    def opponent_step_draw(self, game):
        self.until(game, lambda current: (current.state.phase, current.state.step) == ("beginning", "draw")
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")
        self.assertEqual(1, self.draws(game, "A", draw_step=True), "first completed draw stays with A")

    def draw_card(self, game, seat, *, ref):
        self.add(game.engine, "Island", seat=seat, ref=f"mana-{ref}")
        return self.add(game.engine, "Reach Through Mists", seat=seat, zone="hand", ref=ref)

    def replacement_options(self, game, actor):
        decision = game.packet("pilot:"+actor, full=True)["decision"]
        self.assertEqual("draw.replacement", decision["kind"])
        return decision["ctx"]["options"]

    def choose_replacement(self, game, actor, source_ref):
        options = self.replacement_options(game, actor)
        option, = (row["id"] for row in options if row["label"].startswith(source_ref+":"))
        result = game.act("pilot:"+actor, {"action_id": "choose", "choice": option})
        self.assertTrue(result.ok, result.summary)

    def draw_replacements(self, game):
        return tuple(rule for rule in game.state.continuous_effects if isinstance(rule, ResolvedDrawRedirect))

    def cast_with_player_target(self, game, actor, card, target):
        self.priority(game, actor)
        offered = self.action(game, actor, card)
        before = authoritative_state_hash(game.state)
        bad = game.act("pilot:"+actor, {"action_id": offered["id"], "targets": ["missing"], "pay": "auto"})
        self.assertFalse(bad.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:"+actor, {"action_id": offered["id"], "targets": [target], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_upkeep_and_later_draws_redirect_but_ordinary_draw_step_draw_does_not(self):
        game = self.session(121071001)
        source = self.add(game.engine, "Notion Thief", seat="B")
        upkeep = self.draw_card(game, "A", ref="upkeep-draw")
        later = self.draw_card(game, "A", ref="later-draw")
        self.assert_closed(game, source)
        self.prepare(game)
        upkeep_witness.VanishingRuntimeTests.upkeep(self, game, seat="A")
        self.checkpoint(game)
        self.cast_for(game, "A", upkeep)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 1), tuple(self.draws(game, seat) for seat in "AB"))
        self.opponent_step_draw(game)
        self.cast_for(game, "A", later)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 2), tuple(self.draws(game, seat) for seat in "AB"))
        self.replay(game, load=True)

    def test_actual_each_affected_player_orders_remaining_replacements_in_private(self):
        game = self.session(121071002)
        seized = self.draw_card(game, "A", ref="draw-with-three-choices")
        thieves = {seat: self.add(game.engine, "Notion Thief", seat=seat, ref=f"thief-{seat}")
                  for seat in "BCD"}
        for card in thieves.values():
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        initial = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        d_draw = game.state.cards[initial["D"][-1]].ref
        self.cast_for(game, "A", seized)
        self.assertIsNotNone(self.resolve(game))
        self.assertEqual(3, len(self.replacement_options(game, "A")))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.choose_replacement(game, "A", thieves["B"].ref)
        self.assertEqual(2, len(self.replacement_options(game, "B")))
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        game = self.replay(game, load=True)
        self.choose_replacement(game, "B", thieves["C"].ref)
        self.assertEqual((0, 0, 0, 1), tuple(self.draws(game, seat) for seat in "ABCD"))
        for seat in "ABC":
            self.assertEqual(initial[seat], tuple(game.state.players[seat].zones["library"]))
            self.assertNotIn(d_draw, json.dumps(game.packet("pilot:"+seat, full=True)))
        self.assertEqual(initial["D"][:-1], tuple(game.state.players["D"].zones["library"]))
        self.assertIn(d_draw, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_actual_redirect_applies_the_receiving_players_draw_restriction(self):
        game = self.session(121071003)
        thief = self.add(game.engine, "Notion Thief", seat="B")
        spirit = self.add(game.engine, "Spirit of the Labyrinth", seat="C")
        first = self.draw_card(game, "A", ref="first-redirected")
        second = self.draw_card(game, "A", ref="second-redirected")
        for card in (thief, spirit):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        for spell in (first, second):
            self.cast_for(game, "A", spell)
            self.assertIsNone(self.resolve(game))
            self.assertEqual((0, 1), tuple(self.draws(game, seat) for seat in "AB"))
        self.replay(game, load=True)

    def test_actual_temporary_redirect_survives_save_load_and_expires_in_cleanup(self):
        game = self.session(121071004)
        redirect = self.add(game.engine, "Plagiarize", seat="B", zone="hand")
        self.assert_closed(game, redirect)
        first = self.draw_card(game, "A", ref="during-temporary-redirect")
        next_turn = self.draw_card(game, "A", ref="after-cleanup")
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"U": 1, "C": 3})
        self.checkpoint(game)
        self.cast_with_player_target(game, "B", redirect, "A")
        effect, = self.draw_replacements(game)
        self.assertEqual(("A", "B"), (effect.target_player, effect.controller))
        self.assertEqual(effect, continuous_journal_effect_from_dict(effect.to_dict()))
        game = self.replay(game, load=True)
        receiver_top = game.state.players["B"].zones["library"][-1]
        receiver_ref = game.state.cards[receiver_top].ref
        self.cast_for(game, "A", first)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 1), tuple(self.draws(game, seat) for seat in "AB"))
        self.assertIn(receiver_ref, json.dumps(game.packet("pilot:B", full=True)))
        for seat in "ACD":
            self.assertNotIn(receiver_ref, json.dumps(game.packet("pilot:"+seat, full=True)))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "cleanup.discard")
        self.assertEqual(1, len(self.draw_replacements(game)))
        cleanup = game.packet("pilot:A", full=True)["decision"]["ctx"]
        options = [row["id"] for row in cleanup["hand"] if row["id"] != next_turn.ref]
        discarded = game.act("pilot:A", {"action_id": "discard", "cards": options[:cleanup["count"]]})
        self.assertTrue(discarded.ok, discarded.summary)
        self.until(game, lambda current: current.state.turn_sequence > turn
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")
        self.assertEqual((), self.draw_replacements(game))
        self.cast_for(game, "A", next_turn)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, self.draws(game, "A"))
        self.replay(game, load=True)

    def test_actual_targeting_the_controller_creates_one_draw_with_no_recursion(self):
        game = self.session(121071005)
        redirect = self.add(game.engine, "Plagiarize", zone="hand")
        self.assert_closed(game, redirect)
        spell = self.draw_card(game, "A", ref="self-targeted-draw")
        self.prepare(game, mana={"U": 1, "C": 3})
        self.checkpoint(game)
        self.cast_with_player_target(game, "A", redirect, "A")
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 0), tuple(self.draws(game, seat) for seat in "AB"))
        self.replay(game, load=True)

    def test_actual_departed_recipient_leaves_the_skipped_draw_in_effect(self):
        game = self.session(121071006)
        redirect = self.add(game.engine, "Plagiarize", seat="B", zone="hand")
        self.assert_closed(game, redirect)
        spell = self.draw_card(game, "A", ref="draw-after-recipient-departure")
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"U": 1, "C": 3})
        self.checkpoint(game)
        self.cast_with_player_target(game, "B", redirect, "A")
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "confirm_concede": True})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["B"].in_game)
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, self.draws(game, "A"))
        self.replay(game, load=True)

    def test_temporary_redirect_declares_its_target_and_rejects_a_malformed_record(self):
        game = self.session(121071007)
        card = self.add(game.engine, "Plagiarize", zone="hand")
        self.assert_closed(game, card)
        program, = (program for program in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if DRAW_REDIRECT_CAPABILITY in program.capability_dependencies)
        self.assertEqual(
            {"target.revalidate_resolution", "zone.draw.result_generated_ordering", DRAW_REDIRECT_CAPABILITY},
            set(temporary_draw_redirect_runtime_dependencies(program)),
        )
        with self.assertRaisesRegex(ValueError, "revalidated player target"):
            temporary_draw_redirect_runtime_dependencies(replace(program, target_schema=None))
        state_rule = ResolvedDrawRedirect("effect", "source", 1, "A", "B")
        for key, wrong in (("schema_version", True), ("timestamp", True), ("duration", "permanent")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                continuous_journal_effect_from_dict({**state_rule.to_dict(), key: wrong})

    def test_mutant_that_redirects_the_first_actual_draw_is_detected_in_native_play(self):
        game = self.session(121071008)
        self.add(game.engine, "Notion Thief", seat="B")
        self.prepare(game)
        upkeep_witness.VanishingRuntimeTests.upkeep(self, game, seat="A")
        self.checkpoint(game)
        with patch("quorune.semantic_runtime.draw_replacements.is_first_draw_in_own_draw_step", return_value=False):
            with self.assertRaisesRegex(AssertionError, "first completed draw stays with A"):
                self.opponent_step_draw(game)

    def test_mutant_that_binds_the_target_as_the_receiver_is_detected_in_native_play(self):
        game = self.session(121071009)
        redirect = self.add(game.engine, "Plagiarize", seat="B", zone="hand")
        spell = self.draw_card(game, "A", ref="redirect-recipient-mutation")
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"U": 1, "C": 3})
        self.checkpoint(game)
        self.cast_with_player_target(game, "B", redirect, "A")
        with patch("quorune.semantic_runtime.temporary_draw_redirect.CreateResultDraws",
                   side_effect=lambda count, recipient: CreateResultDraws(count=count, recipient="A")):
            self.cast_for(game, "A", spell)
            self.assertIsNone(self.resolve(game))
        with self.assertRaises(AssertionError):
            self.assertEqual((0, 1), tuple(self.draws(game, seat) for seat in "AB"))
