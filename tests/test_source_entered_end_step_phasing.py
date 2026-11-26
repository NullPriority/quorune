"""The original entered planeswalker phases at its own end step only once."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.carddb import CardDatabase
from quorune.continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class SourceEnteredEndStepPhasingTests(InheritedNativeFixture, unittest.TestCase):
    playing = untap_fixture.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-entered-end-step-phasing.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-entered-end-step-phasing-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("One source entry for the end step", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_named_source_phase_and_all_three_loyalty_abilities(self):
        admitted, semantics = register_card(self.db, self.registry, "Kaito Shizuki")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Kaito Shizuki", fuzzy=False)
        programs = semantics.programs_for_oracle(card.oracle_id)
        phase, = (program for program in programs if program.event == "step.begin")
        gate, = (item for item in phase.event_condition["all"]
                 if item.get("field") == FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD)
        self.assertEqual("source_entered_this_turn", gate["condition"]["kind"])
        self.assertEqual([{"op": "phase_out_permanent", "schema_version": 1, "subject": "source"}], phase.effects)
        self.assertEqual({1, -2, -7}, {program.cost_schema["loyalty_delta"]
                                    for program in programs if program.event == "activate"})
        altered = replace(card, oracle_text=card.oracle_text.replace("if Kaito entered this turn",
                                                                     "if another permanent entered this turn"))
        self.assertNotEqual("exact", compile_oracle_card(
            altered, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def activate_plus_one(self, game, source):
        self.priority(game, "A")
        ability, = (item for item in game.engine._activated_abilities(self.current(game, source))
                    if item.loyalty_delta == 1)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = (row for row in actions if row["id"] == f"activate:{source.ref}:{ability.ability_id}")
        result = game.act("pilot:A", {"action_id": offered["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(4, self.current(game, source).counters["loyalty"])

    def end_phase_trigger(self, game, source):
        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "end_step"
                   and any(item.source_object_id == source.object_id for item in current.state.stack))
        item, = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual("A", item.controller)

    def test_actual_loyalty_discard_then_phasing_keeps_identity_and_does_not_repeat_after_phasing_in(self):
        game = self.playing(70226001)
        source = self.add(game.engine, "Kaito Shizuki", zone="hand")
        drawn = self.add(game.engine, "Plains", zone="library", ref="kaito-private-draw")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "U": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        original = self.current(game, source).logical_object_id
        self.assertEqual(3, self.current(game, source).counters["loyalty"])
        self.activate_plus_one(game, source)
        hand = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (hand.kind, hand.actors))
        self.assertEqual("hand", self.current(game, drawn).zone)
        game = self.replay(game, load=True)
        answered = game.act("pilot:A", {"action_id": "choose", "cards": [drawn.ref]})
        self.assertTrue(answered.ok, answered.summary)
        self.assertIsNone(self.resolve(game))
        self.end_phase_trigger(game, source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((True, original, 4), (self.current(game, source).phased_out,
            self.current(game, source).logical_object_id, self.current(game, source).counters["loyalty"]))
        for seat in ("B", "C", "D", "A"):
            self.until(game, lambda current: current.state.active_player == seat and current.state.step == "upkeep")
        self.assertEqual((False, original, 4), (self.current(game, source).phased_out,
            self.current(game, source).logical_object_id, self.current(game, source).counters["loyalty"]))
        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "end_step")
        self.assertFalse(any(item.source_object_id == source.object_id for item in game.state.stack))
        self.assertFalse(self.current(game, source).phased_out)
        self.replay(game, load=True)

    def test_actual_attack_retains_loyalty_draw_and_leaving_in_response_prevents_phasing(self):
        game = self.playing(70226002)
        source = self.add(game.engine, "Kaito Shizuki", zone="hand")
        prior = self.add(game.engine, "Grizzly Bears")
        prior.acquired_control_turn_count = -1
        removal = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B", ref="kaito-response-one")
        self.add(game.engine, "Island", seat="B", ref="kaito-response-two")
        for name in ("Island", "Plains", "Swamp"):
            self.add(game.engine, name)
        drawn = self.add(game.engine, "Plains", zone="library", ref="kaito-raid-draw")
        for card in (source, removal):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(prior, "C")])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.activate_plus_one(game, source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self.current(game, drawn).zone)
        self.end_phase_trigger(game, source)
        self.cast_for(game, "B", removal, [self.current(game, source).ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("hand", False), (self.current(game, source).zone, self.current(game, source).phased_out))
        self.replay(game, load=True)
