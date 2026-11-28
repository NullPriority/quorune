"""An actual optional ability counter can suppress its surviving original source."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.continuous_effect_state import ResolutionEffectSource, active_resolution_effects
from quorune.control_effects import prepare_tracked_source_presence_duration
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_continuity import SourceContinuityHistory
from quorune.stack_counter import COUNTERED_ABILITY_SOURCE_LOSS_FIELD
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class CounteredAbilitySourceLossTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "countered-ability-source-loss.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/countered-ability-source-loss-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Countered ability source loss", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_flash_and_exact_optional_ability_source_loss(self):
        admitted, semantics = register_card(self.db, self.registry, "Tishana's Tidebinder")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        record = self.db.lookup("Tishana's Tidebinder", fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual(["cast.permission", "permanent.enter.self"], sorted(program.event for program in programs))
        trigger, = (program for program in programs if program.event == "permanent.enter.self")
        self.assertEqual({"zones": ["stack"], "categories": ["ability"], "source_exclusion": True, "up_to": 1},
                         trigger.target_schema)
        effect, = trigger.effects
        self.assertEqual(1, effect[COUNTERED_ABILITY_SOURCE_LOSS_FIELD])
        for changed in (replace(trigger, event="permanent.enter"),
                        replace(trigger, target_schema={**trigger.target_schema, "categories": ["spell"]}),
                        replace(trigger, effects=[{**effect, COUNTERED_ABILITY_SOURCE_LOSS_FIELD: True}])):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        oracle = record.oracle_text.replace("or planeswalker", "or enchantment")
        self.assertNotEqual("exact", compile_oracle_card(
            replace(record, oracle_text=oracle), capability_registry=self.registry,
            capability_profile="commander_review",
        ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def test_source_presence_interval_rejects_an_earlier_phase_out_and_return(self):
        initial = SourceContinuityHistory()
        card = SimpleNamespace(
            logical_object_id="original", zone="battlefield", controller="A", phased_out=False,
            source_continuity=replace(initial, phase_epoch=1, last_phase_in_timestamp=8),
        )
        state = SimpleNamespace(cards={"source": card})
        source = ResolutionEffectSource("trigger", "source", "original", "public")
        self.assertIsNone(prepare_tracked_source_presence_duration(state, source, initial.snapshot(), 9))

    def activation(self, game, card):
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in actions if row["id"].startswith(f"activate:{card.ref}:")]

    def activate(self, game, card, target):
        offered, = self.activation(game, card)
        result = game.act("pilot:B", {"action_id": offered["id"], "targets": [target], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        return game.state.stack[-1].ref

    def suppression(self, game, card):
        return any(operation.op == "remove_all_abilities"
                   for effect in active_resolution_effects(game.state, self.current(game, card))
                   for operation in effect.operations)

    def test_actual_declined_target_preserves_the_ability_below_the_flash_creature(self):
        game = self.playing(61126101)
        tide = self.add(game.engine, "Tishana's Tidebinder", zone="hand")
        creature = self.add(game.engine, "Prodigal Pyromancer", seat="B")
        creature.acquired_control_turn_count = -1
        for card in (tide, creature):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        game.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.checkpoint(game)
        self.activate(game, creature, "A")
        self.cast_for(game, "A", tide)
        self.target(game, "A", [])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["A"].life)
        self.assertFalse(self.suppression(game, creature))
        self.replay(game, load=True)

    def test_actual_counters_and_locks_the_original_source_until_tidebinder_phases_out(self):
        game = self.playing(61126102)
        tide = self.add(game.engine, "Tishana's Tidebinder", zone="hand")
        creature = self.add(game.engine, "Prodigal Pyromancer", seat="B")
        untap = self.add(game.engine, "Twiddle", seat="B", zone="hand")
        grant = self.add(game.engine, "Jump", zone="hand")
        phase = self.add(game.engine, "Reality Ripple", zone="hand")
        creature.acquired_control_turn_count = -1
        for card in (tide, creature, untap, grant, phase):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"U": 1})
        game.state.players["A"].mana_pool.update({"C": 3, "U": 3})
        self.checkpoint(game)
        ability = self.activate(game, creature, "A")
        self.cast_for(game, "A", tide, reserve={"C": 1, "U": 2})
        self.target(game, "A", [ability])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertTrue(self.suppression(game, creature))
        self.cast_for(game, "B", untap, [creature.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        chosen = game.act("pilot:B", {"action_id": "choose", "choice": "untap"})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.current(game, creature).tapped)
        self.assertEqual([], self.activation(game, creature))
        self.cast_for(game, "A", grant, [creature.ref], reserve={"C": 1, "U": 1})
        self.assertIsNone(self.resolve(game))
        self.assertIn("flying", game.engine._combat_keywords(self.current(game, creature)))
        self.assertTrue(self.suppression(game, creature))
        self.cast_for(game, "A", phase, [tide.ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.current(game, tide).phased_out)
        self.assertFalse(self.suppression(game, creature))
        self.assertEqual(1, len(self.activation(game, creature)))
        for seat in ("C", "D", "A"):
            self.until(game, lambda current: current.state.active_player == seat and current.state.step == "upkeep")
        self.assertFalse(self.current(game, tide).phased_out)
        self.assertFalse(self.suppression(game, creature))
        self.replay(game, load=True)

    def test_actual_counter_still_succeeds_after_the_tidebinder_departs_or_its_subject_blinks(self):
        game = self.playing(61126103)
        tide = self.add(game.engine, "Tishana's Tidebinder", zone="hand")
        creature = self.add(game.engine, "Prodigal Pyromancer", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        creature.acquired_control_turn_count = -1
        for card in (tide, creature, bounce, blink):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"U": 1, "W": 1})
        game.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.checkpoint(game)
        original = creature.logical_object_id
        ability = self.activate(game, creature, "A")
        self.cast_for(game, "A", tide)
        self.target(game, "A", [ability])

        departed = self.replay(game, load=True)
        self.cast_for(departed, "B", bounce, [tide.ref])
        self.assertIsNone(self.resolve(departed))
        self.assertEqual("hand", self.current(departed, tide).zone)
        self.assertEqual(40, departed.state.players["A"].life)
        self.assertFalse(self.suppression(departed, creature))
        self.replay(departed, load=True)

        self.cast_for(game, "B", blink, [creature.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(original, self.current(game, creature).logical_object_id)
        self.assertEqual(40, game.state.players["A"].life)
        self.assertFalse(self.suppression(game, creature))
        self.replay(game, load=True)

    def test_actual_counter_stops_a_trigger_and_suppresses_its_surviving_creature(self):
        game = self.playing(61126105)
        tide = self.add(game.engine, "Tishana's Tidebinder", zone="hand")
        wall = self.add(game.engine, "Wall of Omens", seat="B", zone="hand")
        for card in (tide, wall):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"W": 2})
        game.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", wall)
        self.resolve_top(game)
        draw, = game.state.stack
        self.assertEqual("triggered_ability", draw.kind)
        old_hand = tuple(game.state.players["B"].zones["hand"])
        self.cast_for(game, "A", tide)
        self.target(game, "A", [draw.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(old_hand, tuple(game.state.players["B"].zones["hand"]))
        self.assertTrue(self.suppression(game, wall))
        self.replay(game, load=True)

    def test_actual_counter_uses_current_permanent_type_instead_of_activation_type(self):
        game = self.playing(61126104)
        tide = self.add(game.engine, "Tishana's Tidebinder", zone="hand")
        god = self.add(game.engine, "Thassa, God of the Sea", seat="B")
        devotion = self.add(game.engine, "Arcanis the Omnipotent", seat="B")
        wing = self.add(game.engine, "Wind Drake", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (tide, god, devotion, wing, bounce):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"U": 3})
        game.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.checkpoint(game)
        self.assertIn("creature", game.engine._effective_type_parts(self.current(game, god))[0])
        ability = self.activate(game, god, wing.ref)
        self.cast_for(game, "A", tide)
        self.target(game, "A", [ability])
        self.cast_for(game, "B", bounce, [devotion.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"enchantment"}, game.engine._effective_type_parts(self.current(game, god))[0])
        self.assertFalse(self.suppression(game, god))
        self.assertIn("indestructible", game.engine._combat_keywords(self.current(game, god)))
        self.assertTrue(any(event.code == "stack.counter" for event in game.state.events))
        self.replay(game, load=True)
