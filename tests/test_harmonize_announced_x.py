"""Harmonize quotes one current optional tap and retains announced search X."""

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
from quorune.harmonize import ANNOTATION, CAPABILITY, HANDLER_ID, HarmonizeTapPayment
from quorune.haste import is_summoning_sick
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


CARD = "Nature's Rhythm"
PROFILE = "commander_review"


class HarmonizeAnnouncedXTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "harmonize-announced-x.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "ability-source-counter-cards.json",
            fixtures / "activation-result-views-cards.json",
            fixtures / "announced-x-library-search-cards.json",
            fixtures / "harmonize-announced-x-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Harmonize announced X", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def start(self, game, spell, *, mana, b_mana=None):
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, spell)
        self.prepare(game, mana=mana)
        game.state.players["B"].mana_pool.update(b_mana or {})
        self.checkpoint(game)

    def reject(self, game, seat, command):
        initial = authoritative_state_hash(game.state)
        self.assertFalse(game.act(f"pilot:{seat}", command).ok)
        self.assertEqual(initial, authoritative_state_hash(game.state))

    def cast_command(self, game, spell, option, x, *, tap=None):
        action = self.action(game, "A", spell)
        return {"action_id": action["id"], "cost_option": option, "x": x, "pay": "auto",
                **({"tap_cost_cards": [tap.ref]} if tap is not None else {})}

    def search(self, game, maximum):
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual({"maximum": maximum}, decision["ctx"]["search_spec"]["selector"]["mana_value"])
        return decision["legal_actions"][0]["choice_schema"]["legal_refs"]

    def test_graveyard_action_quotes_current_power_reserves_the_tap_and_keeps_search_x(self):
        game = self.session(70218031)
        spell = self.add(game.engine, CARD, zone="graveyard")
        creature = self.add(game.engine, "Grizzly Bears", ref="harmonize-current-power")
        creature.counters["+1/+1"] = 1
        creature.acquired_control_turn_count = max(1, game.state.players["A"].turns_begun)
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="harmonize-foreign")
        tapped = self.add(game.engine, "Grizzly Bears", ref="harmonize-tapped")
        tapped.tapped = True
        found = self.add(game.engine, "Grizzly Bears", zone="library", ref="harmonize-private-result")
        too_large = self.add(game.engine, "Master Transmuter", zone="library")
        self.start(game, spell, mana={"G": 4})
        self.assertTrue(is_summoning_sick(game.engine, creature))
        quote = self.action(game, "A", spell)
        options = {option["id"]: option for option in quote["cost_options"]}
        selected = f"harmonize:{creature.ref}"
        self.assertEqual({"harmonize", selected}, set(options))
        self.assertEqual(0, options["harmonize"]["choice_schema"]["x"]["maximum"])
        self.assertEqual(3, options[selected]["choice_schema"]["x"]["maximum"])
        self.assertEqual([creature.ref], options[selected]["choice_schema"]["tap_cost_cards"]["legal_refs"])
        self.assertEqual(4, options[selected]["requirements"]["G"])
        command = self.cast_command(game, spell, selected, 3, tap=creature)
        for principal, submission in (
            ("B", command), ("A", {**command, "x": 4}), ("A", {**command, "x": 4, "power": 40}),
            ("A", {**command, "tap_cost_cards": [foreign.ref]}),
            ("A", {**command, "tap_cost_cards": [tapped.ref]}),
            ("A", {**command, "tap_cost_cards": [creature.ref, creature.ref]}),
            ("A", {**command, "cost_option": "harmonize"}),
        ):
            self.reject(game, principal, submission)
        outcome = game.act("pilot:A", command)
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertTrue(game.state.cards[creature.object_id].tapped)
        self.assertFalse(game.state.cards[foreign.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.assertEqual(3, game.state.stack[-1].x_value)
        self.assertIs(game.state.cards[spell.object_id].annotations.get(ANNOTATION), True)
        self.assertEqual([found.ref], self.search(game, 3))
        self.assertNotIn(found.ref, json.dumps(game.packet("pilot:B", full=True)))
        game = self.replay(game, load=True)
        self.reject(game, "A", {"action_id": "choose", "search_cards": [too_large.ref]})
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": [found.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "exile"), tuple(game.state.cards[card.object_id].zone for card in (found, spell)))
        self.replay(game, load=True)

    def test_negative_power_can_pay_the_tap_without_reducing_colored_or_generic_price(self):
        game = self.session(70218032)
        spell = self.add(game.engine, CARD, zone="graveyard")
        creature = self.add(game.engine, "Ornithopter")
        creature.counters["-1/-1"] = 1
        self.start(game, spell, mana={"C": 1, "G": 4})
        self.assertEqual(-1, game.engine._numeric_stat(creature.object_id, "power"))
        selected = f"harmonize:{creature.ref}"
        prices = {row["id"]: row for row in self.action(game, "A", spell)["cost_options"]}
        self.assertEqual(1, prices[selected]["choice_schema"]["x"]["maximum"])
        self.assertEqual(1, prices["harmonize"]["choice_schema"]["x"]["maximum"])
        self.reject(game, "A", self.cast_command(game, spell, selected, 2, tap=creature))
        outcome = game.act("pilot:A", self.cast_command(game, spell, selected, 1, tap=creature))
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertTrue(game.state.cards[creature.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual([], self.search(game, 1))
        outcome = game.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_insufficient_colored_mana_cannot_be_replaced_with_power_or_colorless(self):
        game = self.session(70218033)
        spell = self.add(game.engine, CARD, zone="graveyard")
        creature = self.add(game.engine, "Grizzly Bears")
        creature.counters["+1/+1"] = 4
        self.start(game, spell, mana={"C": 5, "G": 3})
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(action.get("card") == spell.ref for action in actions))

    def test_hand_cast_has_same_announced_search_and_ordinary_disposition(self):
        game = self.session(70218034)
        spell = self.add(game.engine, CARD, zone="hand")
        found = self.add(game.engine, "Llanowar Elves", zone="library")
        self.start(game, spell, mana={"C": 1, "G": 2})
        result = game.act("pilot:A", self.cast_command(game, spell, "normal", 1))
        self.assertTrue(result.ok, result.summary)
        self.assertNotIn(ANNOTATION, game.state.cards[spell.object_id].annotations)
        self.assertEqual([found.ref], self.search(game, 1))
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("library", "graveyard"), tuple(game.state.cards[card.object_id].zone for card in (found, spell)))
        self.replay(game, load=True)

    def test_counter_exiles_paid_original_and_copy_keeps_x_without_the_designation(self):
        game = self.session(70218035)
        spell = self.add(game.engine, CARD, zone="graveyard")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        self.start(game, spell, mana={"C": 1, "G": 4}, b_mana={"U": 2})
        result = game.act("pilot:A", self.cast_command(game, spell, "harmonize", 1))
        self.assertTrue(result.ok, result.summary)
        original, = game.state.stack
        copy = game.engine._copy_stack_item(controller="A", target=original, targets=(), target_groups={},
                                            reason="Harmonize original-copy disposition fixture")
        self.assertEqual(1, copy.x_value)
        self.assertNotIn(ANNOTATION, game.state.cards[copy.card_object_id].annotations)
        self.checkpoint(game)
        self.cast(game, "B", counter, [original.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        game = self.replay(game, load=True)
        self.assertEqual([], self.search(game, 1))
        outcome = game.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn(copy.card_object_id, game.state.players["A"].zones["exile"])
        self.replay(game, load=True)

    def test_whole_card_binding_and_mutated_price_fail_closed(self):
        card = self.db.lookup(CARD, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        search, keyword = ir.faces[0].nodes
        self.assertEqual("search", search.effects[0]["op"])
        self.assertEqual(HANDLER_ID, keyword.handlers[0]["handler_id"])
        self.assertIn(CAPABILITY, keyword.capability_dependencies)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = (program for program in semantics.programs_for_oracle(card.oracle_id) if program.handlers)
        deps = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(missing, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])
        for bad in ("{X}{X}{G}", "{G}{G}", "{X}{G/U}"):
            changed = replace(card, oracle_text=card.oracle_text.replace("Harmonize {X}{G}{G}{G}{G}", "Harmonize " + bad))
            self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        with self.assertRaises(ValueError):
            HarmonizeTapPayment("id", "ref", "logical", True)
        with patch("quorune.compiler.harmonize_nodes.harmonize_keyword_node", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)
