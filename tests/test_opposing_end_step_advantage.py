"""Independent current opposing-end-step creature and land checks with exact replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opposing_end_step_advantage import CAPABILITY, opposing_end_step_advantage_condition
from scripts.build_test_database import build_fixture_database

CARD = "Keeper of the Accord"
PROFILE = "commander_review"


class OpposingEndStepAdvantageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "opposing-end-step.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "opposing-end-step-advantage-cards.json",
            "state-qualified-zone-cards.json", "activated-self-transform-cards.json",
            "ability-source-counter-cards.json", "colorless-nonland-target-cards.json",
            "controller-turn-basic-land-threshold-power-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Opposing end-step comparisons", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_compiles_independent_exact_conditions_and_current_result_bindings(self):
        pinned = self.db.lookup(CARD, fuzzy=False)
        record = replace(query_record(pinned.oracle_text), type_line=pinned.type_line, mana_cost="{3}{W}")
        proof = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", proof.status, proof.material_residuals)
        for node, metric in zip(proof.faces[0].nodes, ("creature_count", "land_count"), strict=True):
            self.assertEqual(node.text, record.oracle_text[node.span.start:node.span.end])
            self.assertEqual(opposing_end_step_advantage_condition(metric), node.event_condition)
            self.assertIn(CAPABILITY, node.capability_dependencies)
            self.assertIn("intervening_condition", node.runtime_coverage)
        for old, new in (("each opponent's end step", "each player's end step"),
                         ("that player controls more", "an opponent controls more"),
                         ("more lands than you", "at least as many lands as you"),
                         ("basic Plains", "Plains"), ("battlefield tapped", "battlefield")):
            with self.subTest(new=new):
                altered = replace(record, oracle_text=record.oracle_text.replace(old, new))
                result = compile_oracle_card(altered, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)

        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        programs = [value for value in semantics.programs_for_oracle(pinned.oracle_id) if value.event == "step.begin"]
        self.assertEqual(2, len(programs))
        for program in programs:
            dependencies = [value for value in program.capability_dependencies if value != CAPABILITY]
            altered = replace(program, capability_dependencies=dependencies,
                              capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
            self.assertFalse(bind_semantic_program_runtime(altered, capability_registry=self.registry,
                                                           profile=PROFILE)["strict"])

    def end_step_setup(self, game, *, active="B", mana=None):
        self.prepare(game, mana=mana or {}, active=active)
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.priority_player = None
        game.state.phase = "postcombat_main"
        game.state.phase_index = TURN_STEPS.index(("postcombat_main", "main"))
        game.engine._grant_priority(active)
        game.engine.pump()
        self.checkpoint(game)

    def reach_end_step(self, game):
        for _ in range(8):
            if game.state.step == "end_step":
                return
            result = game.act(game.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Did not reach the end-step occurrence")

    def seed(self, seed, *, b_creatures=2, b_lands=2):
        game = self.session(seed)
        keeper = self.add(game.engine, CARD)
        self.assert_closed(game, keeper)
        creatures = [self.add(game.engine, "Grizzly Bears", seat="B", ref=f"b-creature-{i}")
                     for i in range(b_creatures)]
        self.add(game.engine, "Generic Bound Plains", ref="a-land")
        for i in range(b_lands):
            self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"b-land-{i}")
        return game, keeper, creatures

    def order(self, game, *, first):
        pending = game.state.pending_decision
        self.assertEqual(("trigger.order", ["A"]), (pending.kind, pending.actors))
        triggers = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        by_kind = {"creature" if "more creatures" in row["label"] else "land": row["id"] for row in triggers}
        other = "land" if first == "creature" else "creature"
        response = game.act("pilot:A", {"action_id": "order", "triggers": [by_kind[other], by_kind[first]]})
        self.assertTrue(response.ok, response.summary)

    def resolve_top_to_choice_or_finish(self, game):
        current = game.state.stack[-1].ref
        for _ in range(12):
            pending = game.state.pending_decision
            if pending is not None and pending.kind != "priority":
                return pending
            if not any(item.ref == current for item in game.state.stack):
                return None
            receipt = game.act(game.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(receipt.ok, receipt.summary)
        self.fail("Top end-step instruction failed to finish")

    def choice(self, game, choices):
        answer = game.act("pilot:A", {"action_id": "choose", **choices})
        self.assertTrue(answer.ok, answer.summary)

    def test_priority_response_removes_only_the_creature_result_then_searches_tapped_plains(self):
        game, keeper, creatures = self.seed(60345131, b_lands=4)
        murder = self.add(game.engine, "Murder", zone="hand")
        nonbasic_plains = self.add(game.engine, "Tundra", zone="library")
        self.assert_closed(game, murder)
        for i in range(2):
            self.add(game.engine, "Swamp", ref=f"a-swamp-{i}")
        self.end_step_setup(game)
        self.reach_end_step(game)
        game = self.replay(game, load=True)
        self.order(game, first="creature")
        self.assertEqual(["A", "A"], [item.controller for item in game.state.stack])
        self.cast_for(game, "A", murder, [creatures[0].ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve_top_to_choice_or_finish(game))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        pending = self.resolve_top_to_choice_or_finish(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.choice(game, {"choice": "apply"})
        game = self.replay(game, load=True)
        self.assertEqual(("semantic.search", ["A"]),
                         (game.state.pending_decision.kind, game.state.pending_decision.actors))
        before = authoritative_state_hash(game.state)
        selected = game.state.cards[game.state.players["A"].zones["library"][-2]]
        args = {"search_cards": [selected.ref]}
        decision = game.packet("pilot:A", full=True)["decision"]
        refs = {row["id"] for row in decision["ctx"]["search_cards"]}
        self.assertIn(selected.ref, refs)
        self.assertNotIn(nonbasic_plains.ref, refs)
        wrong_land = game.act("pilot:A", {"action_id": "choose", "search_cards": [nonbasic_plains.ref]})
        self.assertFalse(wrong_land.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertNotIn(selected.ref, json.dumps(game.packet("pilot:B", full=True)))
        rejected = game.act("pilot:B", {"action_id": "choose", **args})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        before_shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
        self.choice(game, args)
        entered = game.state.cards[selected.object_id]
        self.assertEqual(("battlefield", "A", True), (entered.zone, entered.controller, entered.tapped))
        self.assertEqual(before_shuffles + 1, game.state.players["A"].stats.get("shuffle_count"))
        self.assertEqual("A", game.state.cards[keeper.object_id].controller)
        self.replay(game, load=True)

    def assert_soldier(self, game, token):
        self.assertEqual("A", token.controller)
        data = game.engine._effective_card_data(token)
        types, subtypes, _ = game.engine._type_parts(data["type_line"])
        self.assertEqual(({"creature"}, {"soldier"}, ["W"]), (set(types), set(subtypes), data["colors"]))
        self.assertEqual(("1", "1"), (str(data["power"]), str(data["toughness"])))

    def test_original_trigger_controller_keeps_the_soldier_after_source_control_changes(self):
        game, keeper, _ = self.seed(60345132, b_lands=1)
        treason = self.add(game.engine, "Act of Treason", seat="B", zone="hand")
        timing = self.add(game.engine, "Vedalken Orrery", seat="B")
        self.assert_closed(game, treason)
        self.assert_closed(game, timing)
        for i in range(3):
            self.add(game.engine, "Mountain", seat="B", ref=f"b-mountain-{i}")
            self.add(game.engine, "Generic Bound Plains", ref=f"additional-a-plains-{i}")
        self.end_step_setup(game)
        self.reach_end_step(game)
        self.assertEqual(1, len(game.state.stack))
        self.cast_for(game, "B", treason, [keeper.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[keeper.object_id].controller)
        self.assertIsNone(self.resolve(game))
        soldier, = [card for card in game.state.cards.values() if card.is_token]
        self.assert_soldier(game, soldier)
        self.replay(game, load=True)

    def test_different_opponent_and_ties_do_not_trigger_and_own_end_step_is_excluded(self):
        for active in ("A", "B"):
            with self.subTest(active=active):
                game, _, _ = self.seed(60345134 + (active == "A"), b_creatures=1, b_lands=1)
                for i in range(3):
                    self.add(game.engine, "Grizzly Bears", seat="C", ref=f"c-creature-{i}")
                    self.add(game.engine, "Generic Bound Plains", seat="C", ref=f"c-land-{i}")
                self.end_step_setup(game, active=active)
                self.reach_end_step(game)
                self.assertFalse(game.state.stack)
                self.assertEqual("priority", game.state.pending_decision.kind)
                self.replay(game, load=True)

    def test_land_recheck_fails_when_a_response_ties_land_counts_but_token_is_independent(self):
        game, _, _ = self.seed(60345136)
        self.add(game.engine, "Generic Bound Plains", ref="second-a-land")
        land = self.add(game.engine, "Great Furnace", seat="B")
        disenchant = self.add(game.engine, "Disenchant", zone="hand")
        self.assert_closed(game, disenchant)
        self.end_step_setup(game)
        self.reach_end_step(game)
        self.order(game, first="land")
        self.cast_for(game, "A", disenchant, [land.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[land.object_id].zone)
        before = game.state.players["A"].stats.get("shuffle_count", 0)
        self.assertIsNone(self.resolve_top_to_choice_or_finish(game))
        self.assertEqual(before, game.state.players["A"].stats.get("shuffle_count", 0))
        self.assertIsNone(self.resolve(game))
        token, = [card for card in game.state.cards.values() if card.is_token]
        self.assert_soldier(game, token)
        self.replay(game, load=True)

    def test_declining_skips_search_and_failing_to_find_still_shuffles(self):
        for search in (False, True):
            with self.subTest(search=search):
                game, _, _ = self.seed(60345138 + search, b_creatures=1)
                self.end_step_setup(game)
                self.reach_end_step(game)
                self.assertEqual(1, len(game.state.stack))
                pending = self.resolve_top_to_choice_or_finish(game)
                self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                before = game.state.players["A"].stats.get("shuffle_count", 0)
                before_lands = tuple(game.state.players["A"].zones["battlefield"])
                self.choice(game, {"choice": "apply" if search else "decline"})
                if search:
                    game = self.replay(game, load=True)
                    pending = game.state.pending_decision
                    self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
                    self.choice(game, {"search_cards": []})
                self.assertEqual(before + int(search), game.state.players["A"].stats.get("shuffle_count", 0))
                self.assertEqual(before_lands, tuple(game.state.players["A"].zones["battlefield"]))
                self.replay(game, load=True)
