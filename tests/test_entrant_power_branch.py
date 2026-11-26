"""Controlled creature entries compare power during resolution through real actions."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.entrant_power_branch import (
    ENTRANT_POWER_BRANCH_CAPABILITY, ENTRANT_POWER_CONTEXT, entrant_power_branch_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Tribute to the World Tree"
PROFILE = "commander_review"


class EntrantPowerBranchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entrant-power.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entrant-power-draw-or-counters-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Entrant power", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana=None, actor="A"):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor=actor, active=actor, mana=mana)
        self.checkpoint(game)

    def draw_history(self, game):
        return len(game.state.players["A"].draw_history)

    def test_actual_duplicate_triggers_each_read_power_and_draw_stays_private(self):
        game = self.session(76210201)
        first = self.add(game.engine, NAME, ref="first-tribute")
        second = self.add(game.engine, NAME, ref="second-tribute")
        entrant = self.add(game.engine, "Llanowar Elves", zone="hand")
        draw_count = self.draw_history(game)
        expected_draw = game.state.cards[game.state.players["A"].zones["library"][-1]]
        self.start(game, first, second, entrant, mana={"G": 1})
        self.cast(game, "A", entrant)
        order = self.resolve(game)
        self.assertEqual(("trigger.order", ["A"]), (order.kind, order.actors))
        group = next(group for group in game.state.pending_trigger_batches[0].groups if group.controller == "A")
        self.assertEqual(2, len(group.items))
        action = game.act("pilot:A", {"action_id": "order", "triggers": [item.ref for item in group.items]})
        self.assertTrue(action.ok, action.summary)
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[entrant.object_id].counters["+1/+1"])
        self.assertEqual(draw_count, self.draw_history(game))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[entrant.object_id].counters["+1/+1"])
        self.assertEqual(draw_count + 1, self.draw_history(game))
        self.assertIn(expected_draw.object_id, game.state.players["A"].zones["hand"])
        for seat in "BCD":
            self.assertNotIn(expected_draw.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_response_power_is_current_or_last_known_at_departure(self):
        for departure in ("none", "low-bounce", "high-bounce", "phased"):
            with self.subTest(departure=departure):
                game = self.session(76210210 + len(departure))
                source = self.add(game.engine, NAME)
                entrant = self.add(game.engine, "Grizzly Bears", zone="hand")
                growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                phase = self.add(game.engine, "Reality Ripple", seat="B", zone="hand")
                self.add(game.engine, "Forest", seat="B")
                self.add(game.engine, "Island", seat="B", ref="first-reaction-island")
                self.add(game.engine, "Island", seat="B", ref="second-reaction-island")
                draw_count = self.draw_history(game)
                self.start(game, source, entrant, growth, bounce, phase, mana={"G": 1, "C": 1})
                self.cast(game, "A", entrant)
                self.resolve_top(game)
                self.assertEqual(1, self.trigger_count(game, source))
                original = game.state.cards[entrant.object_id].ref
                if departure in ("none", "high-bounce", "phased"):
                    self.cast(game, "B", growth, [original])
                    self.resolve_top(game)
                if departure in ("low-bounce", "high-bounce"):
                    self.cast(game, "B", bounce, [original])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[entrant.object_id].zone)
                    self.assertEqual(2 if departure == "low-bounce" else 5,
                                     game.state.stack[-1].context[ENTRANT_POWER_CONTEXT]["power"])
                if departure == "phased":
                    self.cast(game, "B", phase, [original])
                    self.resolve_top(game)
                    self.assertTrue(game.state.cards[entrant.object_id].phased_out)
                    self.assertEqual(5, game.state.stack[-1].context[ENTRANT_POWER_CONTEXT]["power"])
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(draw_count + int(departure != "low-bounce"), self.draw_history(game))
                self.assertEqual(0, game.state.cards[entrant.object_id].counters.get("+1/+1", 0))
                self.replay(game, load=True)

    def test_actual_opponents_entrants_do_not_trigger(self):
        game = self.session(76210202)
        source = self.add(game.engine, NAME)
        entrant = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand")
        self.start(game, source, entrant, actor="B", mana={"G": 1, "C": 1})
        self.cast(game, "B", entrant)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertFalse(game.state.cards[entrant.object_id].counters)
        self.replay(game, load=True)

    def test_whole_source_requires_the_two_branches_and_original_occurrence(self):
        card = self.db.lookup("Tribute to the World Tree", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Tribute to the World Tree")
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn(ENTRANT_POWER_BRANCH_CAPABILITY, entrant_power_branch_runtime_dependencies(program))
        self.assertIsNone(program.target_schema)
        deps = tuple(cap for cap in program.capability_dependencies if cap != ENTRANT_POWER_BRANCH_CAPABILITY)
        altered = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{ENTRANT_POWER_BRANCH_CAPABILITY}",
                      binding["blockers"])
        with self.assertRaises(ValueError):
            entrant_power_branch_runtime_dependencies(replace(program, event_condition=None))
        for old, new in (
            ("you control ", ""), ("its power", "its toughness"), ("on it.", "on target creature."),
            ("on it.", "on it. Change the name of each card in your hand."),
        ):
            altered_card = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                altered_card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.trigger_result_templates._entrant_power_branch_result", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
