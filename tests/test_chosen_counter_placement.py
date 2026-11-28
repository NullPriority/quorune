"""Original public counter-kind selection and optional payment at committed placement."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.chosen_counter_placement import (
    CHOSEN_COUNTER_PLACEMENT_CAPABILITY,
    chosen_counter_placement_effect,
    chosen_counter_placement_target_schema,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_fixed_one_counter_movement as movement_fixture


PROFILE = "commander_review"


class ChosenCounterPlacementTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.FixedOneCounterMovementTests.reject
    ready = movement_fixture.FixedOneCounterMovementTests.ready
    activate = movement_fixture.FixedOneCounterMovementTests.activate
    lands = movement_fixture.FixedOneCounterMovementTests.lands

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-counter-placement.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "attached-counter-prohibition-cards.json",
            fixtures / "chosen-counter-placement-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Chosen counter placement", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Plains", 30),
        ], [commander])

    def test_animation_module_preserves_both_printed_authority_paths(self):
        card = self.db.lookup("Animation Module", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, "Animation Module")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = (program for program in programs if program.event == "counter.put")
        activate, = (program for program in programs if program.event == "activate")
        self.assertIn("trigger.event.normalized_counter_placement", trigger.capability_dependencies)
        payment, = trigger.effects
        self.assertEqual("offer_optional_mana_payment", payment["op"])
        self.assertEqual(1, payment["cost"]["GENERIC"])
        self.assertEqual("Servo Token", payment["effects"][0]["name"])
        self.assertEqual([chosen_counter_placement_effect()], activate.effects)
        self.assertEqual(chosen_counter_placement_target_schema(), activate.target_schema)
        self.assertIn(CHOSEN_COUNTER_PLACEMENT_CAPABILITY, activate.capability_dependencies)
        deps = tuple(cap for cap in activate.capability_dependencies if cap != CHOSEN_COUNTER_PLACEMENT_CAPABILITY)
        for changed in (
            replace(activate, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict()),
            replace(activate, cost_schema={**activate.cost_schema, "tap_source": False}),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for printed, unsupported in (
            ("target permanent or player", "target player"),
            ("another counter of that kind", "two counters of that kind"),
            ("{3}, {T}", "{2}, {T}"),
        ):
            with self.subTest(unsupported=unsupported):
                changed = replace(card, oracle_text=card.oracle_text.replace(printed, unsupported))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)

    def offer_kind(self, game):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        offer, = context["legal_actions"]
        schema = offer["choice_schema"]
        self.assertEqual("counter_kind", schema["field"])
        return schema["legal_values"]

    def choose_kind(self, game, name):
        command = {"action_id": "choose", "counter_kind": name}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "counter_kind": "unoffered"})
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_animation_module_places_the_chosen_player_counter_with_actor_rollback(self):
        game = self.session(76483001)
        source = self.add(game.engine, "Animation Module")
        self.lands(game, "Forest", 3)
        target = game.state.players["B"]
        target.poison = 2
        target.energy = 1
        self.ready(game, source)
        self.activate(game, source, "ab2", ["B"])
        self.assertEqual(["energy", "poison"], self.offer_kind(game))
        game = self.replay(game, load=True)
        self.choose_kind(game, "poison")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.state.players["B"].poison, "The chosen poison kind must be placed")
        self.assertEqual(1, game.state.players["B"].energy)
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_animation_module_witness_detects_a_substituted_player_counter_kind(self):
        from quorune.semantic_choices import chosen_counter_placement

        original = chosen_counter_placement.PlacePlayerCountersIntent

        def substitute_kind(*args, **kwargs):
            return original(*args, **{**kwargs, "counter_name": "energy"})

        with patch.object(chosen_counter_placement, "PlacePlayerCountersIntent", side_effect=substitute_kind):
            with self.assertRaisesRegex(AssertionError, "chosen poison kind"):
                self.test_actual_animation_module_places_the_chosen_player_counter_with_actor_rollback()

    def test_actual_animation_module_offers_one_servo_payment_after_two_committed_counters(self):
        game = self.session(76483002)
        source = self.add(game.engine, "Animation Module")
        self.lands(game, "Forest", 4)
        scales = self.add(game.engine, "Hardened Scales")
        creature = self.add(game.engine, "Grizzly Bears", ref="animation-countered-creature")
        creature.counters.update({"+1/+1": 1, "charge": 2})
        self.ready(game, source, scales)
        self.activate(game, source, "ab2", [creature.ref])
        self.assertEqual(["+1/+1", "charge"], self.offer_kind(game))
        self.choose_kind(game, "+1/+1")
        self.assertEqual({"+1/+1": 3, "charge": 2}, game.state.cards[creature.object_id].counters)
        self.assertEqual(1, self.trigger_count(game, source))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        command = {"action_id": "choose", "pay": True}
        self.reject(game, "C", command)
        game = self.replay(game, load=True)
        paid = game.act("pilot:A", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(game))
        servo, = (card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token)
        self.assertEqual(("Servo Token", "A"), (servo.printed_name, servo.controller))
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_actual_animation_module_does_not_trigger_when_placement_is_prohibited(self):
        game = self.session(76483003)
        source = self.add(game.engine, "Animation Module")
        self.lands(game, "Forest", 3)
        prohibition = self.add(game.engine, "Solemnity", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", ref="animation-guarded-creature")
        creature.counters["+1/+1"] = 1
        self.ready(game, source, prohibition)
        self.activate(game, source, "ab2", [creature.ref])
        self.assertEqual(["+1/+1"], self.offer_kind(game))
        self.choose_kind(game, "+1/+1")
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 1}, game.state.cards[creature.object_id].counters)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.replay(game, load=True)
