"""Native evidence for one committed creature sacrifice and its last known toughness."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.power_sacrifice_model import CONTROLLER_SACRIFICE_LIFE_FIELD, GreatestPowerSacrificeLifeResult
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_choices.apnap_composition import APNAP_PHYSICAL_ORDER_FIELD, ApnapCompositionOrder
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_mutate_cast_native as mutate_witnesses
import test_scheduled_player_triggers as scheduled_witnesses


ORIGINAL = "Doomgape"


class UpkeepSacrificeCreatureToughnessLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "upkeep-sacrifice-creature-toughness-life.sqlite3"
        fixture = ROOT / "tests/fixtures/upkeep-sacrifice-creature-toughness-life-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Upkeep sacrifice toughness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        upkeep, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "step.begin"]
        return card, upkeep

    def test_complete_original_keeps_trample_and_chooses_one_nontargeted_controlled_creature(self):
        card, program = self.program()
        self.assertEqual("battlefield", program.active_zone)
        self.assertIsNone(program.target_schema)
        effect, = program.effects
        self.assertEqual(("choose_cards_apnap", "$controller", 1, "sacrifice"),
                         (effect["op"], effect["actor"], effect["count"], effect["then"]))
        for changed in (
            card.oracle_text.replace("your upkeep", "each upkeep"),
            card.oracle_text.replace("creature's toughness", "creature's mana value"),
            card.oracle_text.replace("sacrifice a creature", "sacrifice a planeswalker"),
            card.oracle_text.replace("toughness.", "toughness. Rewrite history."),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, changed)

    def test_raw_upkeep_result_rejects_different_actor_cardinality_and_schedule(self):
        _, program = self.program()
        effect, = program.effects
        for changed in (
            replace(program, event_condition=None),
            replace(program, coverage=tuple(tag for tag in program.coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(program, effects=({key: value for key, value in effect.items() if key != CONTROLLER_SACRIFICE_LIFE_FIELD},)),
            replace(program, effects=({**effect, "actor": "$context.player"},)),
            replace(program, effects=({**effect, "count": 2},)),
            replace(program, effects=({**effect, "players": "opponents"},)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                profile="commander_review")
            self.assertFalse(binding["strict"], binding)
        old_result = {"schema_version": 1, "player": "B", "incarnation": "selected-creature"}
        self.assertEqual(old_result, GreatestPowerSacrificeLifeResult.from_dict(old_result).to_dict())
        with self.assertRaises(ValueError):
            GreatestPowerSacrificeLifeResult.from_dict({**old_result, "schema_version": 2, "characteristic": "power"})

    def upkeep(self, game, actor):
        scheduled_witnesses.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self, game, actor)

    def choose_sacrifice(self, game, actor, selected, *, ineligible=None):
        decision = game.state.pending_decision
        self.assertEqual(("choice.apnap", [actor]), (decision.kind, decision.actors))
        baseline = authoritative_state_hash(game.state)
        impossible = {"action_id": "choose", "cards": [ineligible.ref] if ineligible is not None else []}
        denied = game.act("pilot:" + actor, impossible)
        self.assertFalse(denied.ok)
        self.assertEqual(baseline, authoritative_state_hash(game.state))
        accepted = game.act("pilot:" + actor, {"action_id": "choose", "cards": [selected.ref]})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_borrowed_source_gains_current_toughness_after_replacement_destination(self):
        game = self.session(50370120)
        source = self.add(game.engine, ORIGINAL)
        game.engine.change_control(source.object_id, "B", reason="Prepare borrowed upkeep source")
        spider = self.add(game.engine, "Giant Spider", seat="B", ref="doomgape-selected-spider")
        foreign = self.add(game.engine, "Grizzly Bears", seat="C", ref="doomgape-foreign-bear")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        rest = self.add(game.engine, "Rest in Peace", seat="D")
        self.add(game.engine, "Forest", seat="B")
        for card in (source, spider, growth, rest):
            self.assert_closed(game, card)
        self.upkeep(game, "B")
        self.assertIn("trample", game.engine._combat_keywords(source))
        trigger, = game.state.stack
        self.assertEqual(("B", source.object_id, []), (trigger.controller, trigger.source_object_id, list(trigger.targets)))
        self.cast_for(game, "B", growth, [spider.ref])
        self.resolve_top(game)
        self.assertEqual((5, 7), tuple(game.engine._numeric_stat(spider.object_id, stat)
                                      for stat in ("power", "toughness")))
        before = {seat: game.state.players[seat].life for seat in "ABCD"}
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        self.assertEqual(before, {seat: game.state.players[seat].life for seat in "ABCD"})
        game = self.replay(game, load=True)
        self.choose_sacrifice(game, "B", spider, ineligible=foreign)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[spider.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
        self.assertEqual({**before, "B": before["B"] + 7}, {seat: game.state.players[seat].life for seat in "ABCD"})
        self.replay(game, load=True)

    def test_actual_only_creature_must_be_sacrificed_and_no_creature_means_no_life(self):
        for murder_first in (False, True):
            with self.subTest(murder_first=murder_first):
                game = self.session(50370130 + murder_first)
                source = self.add(game.engine, ORIGINAL)
                spell = self.add(game.engine, "Murder", seat="B", zone="hand") if murder_first else None
                if spell is not None:
                    self.add(game.engine, "Swamp", seat="B", ref="doomgape-first-black")
                    self.add(game.engine, "Swamp", seat="B", ref="doomgape-second-black")
                    self.add(game.engine, "Plains", seat="B")
                    self.assert_closed(game, spell)
                self.assert_closed(game, source)
                self.upkeep(game, "A")
                before = game.state.players["A"].life
                if spell is not None:
                    self.cast_for(game, "B", spell, [source.ref])
                    self.resolve_top(game)
                pending = self.resolve(game)
                if pending is not None:
                    self.assertFalse(murder_first)
                    self.choose_sacrifice(game, "A", source)
                    self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual(before + (0 if murder_first else 10), game.state.players["A"].life)
                self.replay(game, load=True)

    def test_actual_composed_sacrifice_keeps_its_stat_through_physical_ordering(self):
        game = self.session(50370140)
        source = self.add(game.engine, ORIGINAL)
        bear = self.add(game.engine, "Grizzly Bears")
        heron = self.add(game.engine, "Dreamtail Heron", zone="hand")
        for card in (source, bear, heron):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 1})
        self.checkpoint(game)
        mutate_witnesses.MutateCastNativeTests.mutate(self, game, heron, bear)
        game = mutate_witnesses.MutateCastNativeTests.choose_merge(self, game, "bottom")
        self.assertIsNone(self.resolve(game))
        self.upkeep(game, "A")
        self.assertEqual(2, game.engine._numeric_stat(bear.object_id, "toughness"))
        before = game.state.players["A"].life
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (pending.kind, pending.actors))
        self.choose_sacrifice(game, "A", bear)
        decision = game.state.pending_decision
        self.assertEqual(("permanent.composition_order", ["A"]), (decision.kind, decision.actors))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual({bear.ref, heron.ref}, {row["id"] for row in options["cards"]})
        frame = ApnapCompositionOrder.from_dict(decision.continuation[APNAP_PHYSICAL_ORDER_FIELD])
        self.assertEqual(
            {"schema_version": 2, "player": "A", "incarnation": bear.logical_object_id, "characteristic": "toughness"},
            frame.intent["result"],
        )
        self.assertEqual(before, game.state.players["A"].life)
        baseline = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "order", "cards": [bear.ref, bear.ref]}).ok)
        self.assertEqual(baseline, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": "order", "cards": [bear.ref, heron.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 2, game.state.players["A"].life)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in (bear, heron)))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)
