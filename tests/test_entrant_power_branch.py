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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.entrant_power_branch import (
    ENTRANT_GREATEST_POWER_DRAW_CAPABILITY, ENTRANT_POWER_BRANCH_CAPABILITY, ENTRANT_POWER_CONTEXT,
    entrant_power_branch_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Tribute to the World Tree"
PROFILE = "commander_review"
GREATEST_DRAW_ORACLE_ID = "1d725121-e50c-42f0-9128-56802f07c89e"


class EntrantPowerBranchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entrant-power.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests/fixtures/entrant-power-draw-or-counters-cards.json",
                ROOT / "tests/fixtures/selvala-entrant-power-cards.json",
                ROOT / "tests/fixtures/source-attack-alone-cards.json",
                ROOT / "tests/fixtures/counter-replacement-cards.json",
            ],
            path,
        )
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

    def greatest_draw_source(self, game, *, zone="battlefield"):
        source = self.add(game.engine, "Selvala, Heart of the Wilds", zone=zone)
        self.assertEqual(GREATEST_DRAW_ORACLE_ID, source.oracle_id)
        return source

    def trigger_draw_choice(self, game, *, seat, choice, unauthorized):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", [seat]), (decision.kind, decision.actors))
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "choice": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + unauthorized, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def test_actual_selvala_other_controller_tie_and_live_power_change(self):
        for index, (boost, larger_opponent) in enumerate(((False, False), (True, False), (True, True))):
            with self.subTest(boost=boost, larger_opponent=larger_opponent):
                game = self.session(402_608_301 + index)
                source = self.greatest_draw_source(game)
                entrant = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand", ref="greatest-entrant")
                competitor = "Ancient Brontodon" if larger_opponent else "Grizzly Bears"
                self.add(game.engine, competitor, seat="D", ref="competing-power")
                growth = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
                self.add(game.engine, "Forest", seat="C")
                self.start(game, source, entrant, actor="B", mana={"C": 1, "G": 1})
                self.cast(game, "B", entrant)
                self.resolve_top(game)
                self.assertEqual(1, self.trigger_count(game, source))
                trigger = game.state.stack[-1]
                self.assertEqual("A", trigger.controller)
                self.assertFalse(trigger.targets)
                draws = {seat: len(game.state.players[seat].draw_history) for seat in "ABCD"}
                top = game.state.players["B"].zones["library"][-1]
                if boost:
                    self.cast(game, "C", growth, [game.state.cards[entrant.object_id].ref])
                    self.resolve_top(game)
                qualifies = boost and not larger_opponent
                if qualifies:
                    game = self.trigger_draw_choice(game, seat="B", choice="apply", unauthorized="A")
                    self.assertIn(top, game.state.players["B"].zones["hand"])
                    hidden = game.state.cards[top].ref
                    for seat in "ACD":
                        self.assertNotIn(hidden, json.dumps(game.packet("pilot:" + seat, full=True)))
                else:
                    self.assertIsNone(self.resolve(game))
                for seat in "ABCD":
                    self.assertEqual(draws[seat] + int(qualifies and seat == "B"), len(game.state.players[seat].draw_history))
                self.replay(game, load=True)

    def test_actual_selvala_uses_departure_power_and_last_controller_for_optional_draw(self):
        game = self.session(402_608_310)
        source = self.greatest_draw_source(game)
        entrant = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand", ref="departing-entrant")
        self.add(game.engine, "Grizzly Bears", seat="D", ref="competing-power")
        theft = self.add(game.engine, "Act of Treason", seat="C", zone="hand")
        growth = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand")
        self.add(game.engine, "Vedalken Orrery", seat="C")
        self.add(game.engine, "Forest", seat="C")
        self.add(game.engine, "Island", seat="C")
        game.state.players["C"].mana_pool.update({"C": 2, "R": 1})
        self.start(game, source, entrant, actor="B", mana={"C": 1, "G": 1})
        self.cast(game, "B", entrant)
        self.resolve_top(game)
        ref = game.state.cards[entrant.object_id].ref
        self.cast(game, "C", theft, [ref])
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[entrant.object_id].controller)
        self.cast(game, "C", growth, [ref])
        self.resolve_top(game)
        self.cast(game, "C", bounce, [ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[entrant.object_id].zone)
        snapshot = game.state.stack[-1].context[ENTRANT_POWER_CONTEXT]
        self.assertEqual((5, "C"), (snapshot["power"], snapshot["controller"]))
        draws = {seat: len(game.state.players[seat].draw_history) for seat in "ABCD"}
        game = self.replay(game, load=True)
        game = self.trigger_draw_choice(game, seat="C", choice="decline", unauthorized="B")
        self.assertEqual(draws, {seat: len(game.state.players[seat].draw_history) for seat in "ABCD"})
        self.replay(game, load=True)

    def test_actual_selvala_mana_reads_current_controlled_power_and_validates_mixed_cost_output(self):
        game = self.session(402_608_320)
        source = self.greatest_draw_source(game, zone="hand")
        own = self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Ancient Brontodon", seat="B")
        self.add(game.engine, "Fervor")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        self.start(game, source, mana={"C": 1, "G": 4})
        self.cast_for(game, "A", source, reserve={"G": 2})
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, source))

        def mana_offer():
            self.priority(game, "A")
            actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            return next(row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))

        self.assertEqual(2, mana_offer()["choice_schema"]["mana_output"]["total"])
        self.cast_for(game, "A", growth, [own.ref], reserve={"G": 1})
        self.resolve_top(game)
        offer = mana_offer()
        allocation = offer["choice_schema"]["mana_output"]
        self.assertEqual(5, allocation["total"])
        self.assertEqual(set("WUBRG"), set(allocation["allowed_colors"]))
        command = {"action_id": offer["id"]}
        before = authoritative_state_hash(game.state)
        for invalid in ({"R": 4}, {"C": 5}):
            self.assertFalse(game.act("pilot:A", {**command, "mana_output": invalid}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {**command, "mana_output": {"R": 2, "U": 3}})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.stack)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        pool = game.state.players["A"].mana_pool
        self.assertEqual((0, 2, 3), (pool.get("G", 0), pool["R"], pool["U"]))
        self.replay(game, load=True)

    def test_selvala_greatest_power_draw_requires_the_original_complete_trigger(self):
        card = self.db.by_oracle_id(GREATEST_DRAW_ORACLE_ID)
        report, semantics = register_card(self.db, self.registry, "Selvala, Heart of the Wilds")
        self.assertTrue(report["strict_capability_ready"], report)
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "creature.enter")
        required = entrant_power_branch_runtime_dependencies(program)
        self.assertIn(ENTRANT_GREATEST_POWER_DRAW_CAPABILITY, required)
        self.assertIn("effect.choice.optional_fixed", required)
        self.assertIsNone(program.target_schema)
        for condition in (None, {"field": "controller", "op": "eq", "value": "$source.controller"}):
            with self.assertRaises(ValueError):
                entrant_power_branch_runtime_dependencies(replace(program, event_condition=condition))
        required = tuple(cap for cap in program.capability_dependencies if cap != ENTRANT_GREATEST_POWER_DRAW_CAPABILITY)
        withdrawn = replace(program, capability_dependencies=required,
                            capability_closure=self.registry.closure(required, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"], binding)
        self.assertIn(f"capability:undeclared_runtime_dependency:{ENTRANT_GREATEST_POWER_DRAW_CAPABILITY}",
                      binding["blockers"])
        for old, new in (("another creature", "a creature"), ("may draw", "draws"), ("greater than", "equal to")):
            synthetic = replace(card, oracle_text=card.oracle_text.replace(old, new, 1))
            self.assertTrue(compile_oracle_card(
                synthetic, capability_registry=self.registry, capability_profile=PROFILE,
            ).material_residuals)
        with patch("quorune.compiler.trigger_result_templates._entrant_power_branch_result", return_value=None):
            compiled = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(compiled.material_residuals)
            self.assertTrue(any("mana.production.public_quantity" in node.capability_dependencies
                                for node in compiled.faces[0].nodes))

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
