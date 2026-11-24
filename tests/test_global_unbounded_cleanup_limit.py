"""Anvil's current all-player cleanup rule retains its independent draw-step trigger."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.maximum_hand_size import (
    MaximumHandSizeError, NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID,
    NO_MAXIMUM_HAND_SIZE_HANDLER_ID, NoMaximumHandSizeSpec, effective_maximum_hand_size,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Anvil of Bogardan"
CAPABILITY = NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID
PROFILE = "commander_review"


class GlobalUnboundedCleanupLimitTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "global-cleanup-limit.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/global-unbounded-cleanup-limit-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("All current cleanup seats", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self):
        record = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(record.oracle_id)
        static, = [entry for entry in programs if any(
            handler.get("handler_id") == NO_MAXIMUM_HAND_SIZE_HANDLER_ID for handler in entry.handlers)]
        draw, = [entry for entry in programs if entry.event == "step.begin"]
        return record, static, draw

    def test_complete_original_keeps_global_cleanup_and_the_ordered_draw_step_sibling(self):
        record, static, draw = self.original_programs()
        handler, = static.handlers
        self.assertEqual({"schema_version": 2, "player_scope": "all_players"}, handler["spec"])
        self.assertIn(CAPABILITY, static.capability_dependencies)
        self.assertEqual(("draw", "choose_cards_apnap"), tuple(effect["op"] for effect in draw.effects))
        self.assertEqual("$context.player", draw.effects[0]["player"])
        self.assertEqual(["$context.player"], list(draw.effects[1]["players"]))
        for line in ("Opponents have no maximum hand size.", "Players have a maximum hand size of two.",
                     "Players have no maximum hand size while this artifact is untapped."):
            ir = compile_oracle_card(replace(record, oracle_text=record.oracle_text.replace(
                "Players have no maximum hand size.", line)), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, line)

    def test_raw_global_scope_withdrawal_keeps_the_historical_controller_codec(self):
        _, static, _draw = self.original_programs()
        current = {"schema_version": 2, "player_scope": "all_players"}
        self.assertEqual(current, NoMaximumHandSizeSpec.from_dict(current).to_dict())
        legacy = {"schema_version": 1}
        self.assertEqual(legacy, NoMaximumHandSizeSpec.from_dict(legacy).to_dict())
        for invalid in (
            {"schema_version": 2}, {**current, "player_scope": "controller"},
            {**current, "schema_version": 1}, {**current, "player_scope": "opponents"},
        ):
            with self.assertRaises(MaximumHandSizeError):
                NoMaximumHandSizeSpec.from_dict(invalid)
        deps = [cap for cap in static.capability_dependencies if cap != CAPABILITY]
        closure = self.registry.closure(deps, profile=PROFILE).to_dict() if deps else None
        result = bind_semantic_program_runtime(replace(static, capability_dependencies=deps, capability_closure=closure),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def scene(self, seed, *, draw_step=False, removal=False):
        game = self.session(seed)
        engine = game.engine
        anvil = self.add(engine, NAME, zone="hand")
        self.add(engine, "Vedalken Orrery")
        extra = self.add(engine, "Counterspell", seat="B", zone="hand", ref="anvil-extra-b")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="anvil-private-d")
        disenchant = None
        if removal:
            self.add(engine, "Thought Vessel")
            self.add(engine, "Gnat Miser", seat="C")
            disenchant = self.add(engine, "Disenchant", seat="B", zone="hand")
            for index in range(2):
                self.add(engine, "Plains", seat="B", ref=f"anvil-removal-{index}")
        for index in range(2):
            self.add(engine, "Plains", ref=f"anvil-price-{index}")
        self.assert_closed(game, anvil)

        engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.started = True
        game.state.active_player = "B"
        game.state.current_turn = replace(game.state.current_turn, player="B")
        game.state.last_normal_turn_player = "B"
        phase, step = ("beginning", "upkeep") if draw_step else ("ending", "end_step")
        game.state.phase = phase
        game.state.step = step
        game.state.phase_index = TURN_STEPS.index((phase, step))
        engine._grant_priority("A")
        engine.pump()
        self.checkpoint(game)

        self.cast_for(game, "A", anvil)
        self.resolve_top(game)
        return game, anvil, extra, secret, disenchant

    def limits(self, game):
        return tuple(effective_maximum_hand_size(game.engine, seat) for seat in "ABCD")

    def test_actual_foreign_draw_uses_its_private_discard_then_keeps_excess_through_cleanup(self):
        game, anvil, extra, secret, _removal = self.scene(40225141, draw_step=True)
        initial = len(game.state.players["B"].zones["hand"])
        self.assertEqual((None, None, None, None), self.limits(game))
        self.until(game, lambda current: current.state.step == "draw" and
                   any(item.source_object_id == anvil.object_id for item in current.state.stack))
        trigger, = game.state.stack
        self.assertEqual("A", trigger.controller)
        self.assertEqual(initial + 1, len(game.state.players["B"].zones["hand"]))
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(["B"], pending.actors)
        self.assertEqual(initial + 2, len(game.state.players["B"].zones["hand"]))
        for principal in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(extra.ref, json.dumps(game.packet(principal, full=True)))
        self.assertIn(extra.ref, json.dumps(game.packet("pilot:B", full=True)))
        discard = {"action_id": "choose", "cards": [extra.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", discard).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", discard)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.cards[extra.object_id].zone)

        self.until(game, lambda current: current.state.active_player != "B"
                   or current.state.pending_decision.kind == "cleanup.discard")
        self.assertEqual("C", game.state.active_player)
        self.assertEqual(initial + 1, len(game.state.players["B"].zones["hand"]))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_source_removal_restores_the_current_controller_and_adjusted_cleanup_limits(self):
        game, anvil, _extra, _secret, removal = self.scene(40225142, removal=True)
        self.assertEqual((None, None, None, None), self.limits(game))
        self.cast_for(game, "B", removal, [anvil.ref])
        self.resolve_top(game)
        self.assertEqual((None, 6, 7, 6), self.limits(game))
        initial = len(game.state.players["B"].zones["hand"])
        self.until(game, lambda current: current.state.pending_decision.kind == "cleanup.discard")
        self.assertEqual(["B"], game.state.pending_decision.actors)
        context = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual(initial - 6, context["count"])
        chosen = [row["id"] for row in context["hand"][:context["count"]]]
        accepted = game.act("pilot:B", {"a": "discard", "cards": chosen})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(6, len(game.state.players["B"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_other_player_limit_kills_using_the_historical_controller_only_scope(self):
        game, _anvil, _extra, _secret, _removal = self.scene(40225143)
        self.assertEqual((None, None, None, None), self.limits(game))
        original = NoMaximumHandSizeSpec.from_dict

        def as_controller(raw):
            if raw.get("schema_version") == 2:
                return NoMaximumHandSizeSpec()
            return original(raw)

        def witness():
            self.assertIsNone(self.limits(game)[1], "Anvil must also exempt the other player")

        witness()
        with patch.object(NoMaximumHandSizeSpec, "from_dict", side_effect=as_controller):
            with self.assertRaisesRegex(AssertionError, "Anvil must also exempt the other player"):
                witness()
