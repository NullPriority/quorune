"""Native evidence for an unconditional controller damage-result life floor."""

from dataclasses import replace
import json
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
from quorune.record import authoritative_state_hash
from quorune.replacement import ReplaceableEvent
from quorune.replacement.applicability import condition_matches
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.damage_results import (
    DamageResultReplacementSourceContext, default_damage_result_replacement_registry,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ALI = "Ali from Cairo"
THIEF = "Fortune Thief"


class ControllerDamageResultLifeFloorTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "controller-damage-result-life-floor.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-damage-result-life-floor-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Damage-result life floor", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def floor_program(self, name):
        original = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        floor, = [program for program in semantics.programs_for_oracle(original.oracle_id)
                  if program.event == "damage.results"]
        return original, floor

    def test_both_complete_originals_bind_one_current_battlefield_damage_result(self):
        for name in (ALI, THIEF):
            with self.subTest(name=name):
                _, program = self.floor_program(name)
                self.assertEqual("battlefield", program.active_zone)
                self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, program.coverage)
                self.assertIsNone(program.target_schema)
                self.assertEqual([], program.effects)
                descriptor, = program.handlers
                self.assertEqual({"affected_player_relation": "source_controller",
                                  "requires_controlled_creature": False}, descriptor["condition"])
                self.assertEqual({"minimum_life": 1}, descriptor["modification"])
        original = self.db.lookup(ALI, fuzzy=False)
        for text in (
            original.oracle_text.replace("1", "2"),
            original.oracle_text.replace("your life", "an opponent's life"),
            "If you control a creature, " + original.oracle_text[0].lower() + original.oracle_text[1:],
            original.oracle_text + " Rewrite history.",
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, text)

    def test_raw_floor_requires_current_ability_and_a_starting_life_of_at_least_one(self):
        _, program = self.floor_program(ALI)
        for changed in (
            replace(program, active_zone="command"),
            replace(program, coverage=tuple(tag for tag in program.coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(program, handlers=[]),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)
        descriptor, = program.handlers
        effect = default_damage_result_replacement_registry().replacement_effect(
            descriptor, DamageResultReplacementSourceContext("floor", "B"),
        )
        for player, life_before, loss, expected in (
            ("B", -2, 3, False), ("B", 0, 3, False), ("B", 1, 3, True),
            ("B", 1, 0, False), ("C", 1, 3, False),
        ):
            with self.subTest(player=player, before=life_before, loss=loss):
                event = ReplaceableEvent(event_id="floor-result", kind="damage.results", affected_player=player,
                    payload={"subject_kind": "player", "life_before": life_before, "life_loss_amount": loss,
                             "life_after_without_replacement": life_before - loss, "controls_creature": False})
                self.assertEqual(expected, condition_matches(effect.conditions, event))

    def test_actual_borrowed_ali_caps_unpreventable_damage_and_direct_loss_bypasses_it(self):
        game = self.session(120400810)
        source = self.add(game.engine, ALI)
        bolt = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand")
        pain = self.add(game.engine, "Flaring Pain", seat="C", zone="hand")
        blood = self.add(game.engine, "Sign in Blood", seat="C", zone="hand")
        game.engine.change_control(source.object_id, "B", reason="Prepare borrowed damage result source")
        game.state.players["B"].life = 2
        for card in (source, bolt, pain, blood):
            self.assert_closed(game, card)
        self.prepare(game, actor="C", active="C", mana={"C": 1, "R": 2, "B": 2})
        self.checkpoint(game)
        self.cast_for(game, "C", pain, reserve={"R": 1, "B": 2})
        self.resolve_top(game)
        self.cast_for(game, "C", bolt, ["B"], reserve={"B": 2})
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual(1, game.state.players["B"].life)
        hits = game.engine._current_turn_history("player_damaged")
        self.assertEqual([3], [event.amount for event in hits if event.target == "B"])
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.cast_for(game, "C", blood, ["B"])
        self.resolve_top(game)
        self.assertFalse(game.state.players["B"].in_game)
        self.assertLess(game.state.players["B"].life, 1)
        self.replay(game, load=True)

    def test_actual_ali_applies_to_a_batch_that_kills_it_and_then_expires(self):
        game = self.session(120400820)
        source = self.add(game.engine, ALI)
        fallout = self.add(game.engine, "Volcanic Fallout", seat="C", zone="hand")
        shot = self.add(game.engine, "Shock", seat="C", zone="hand")
        game.state.players["A"].life = 1
        for card in (source, fallout, shot):
            self.assert_closed(game, card)
        self.prepare(game, actor="C", active="C", mana={"C": 1, "R": 3})
        self.checkpoint(game)
        self.cast_for(game, "C", fallout, reserve={"R": 1})
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertTrue(game.state.players["A"].in_game)
        self.assertEqual(1, game.state.players["A"].life)
        self.assertEqual([2], [event.amount for event in game.engine._current_turn_history("player_damaged")
                               if event.target == "A"])
        self.cast_for(game, "C", shot, ["A"])
        self.resolve_top(game)
        self.assertFalse(game.state.players["A"].in_game)
        self.replay(game, load=True)

    def test_actual_thief_face_down_hides_the_ability_and_turning_up_restores_it(self):
        game = self.session(120400830)
        source = self.add(game.engine, THIEF, zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.add(game.engine, "Mountain", ref="first-morph-mountain")
        self.add(game.engine, "Mountain", ref="second-morph-mountain")
        game.state.players["A"].life = 2
        game.state.players["B"].mana_pool["R"] = 1
        for card in (source, bolt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        cast = game.act("pilot:A", {"action_id": "cast-morph:" + source.ref, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.resolve_top(game)
        self.assertTrue(game.state.cards[source.object_id].face_down)
        for seat in "BCD":
            self.assertNotIn(THIEF, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.cast_for(game, "B", bolt, ["A"])
        self.priority(game, "A")
        for reveal in (False, True):
            with self.subTest(reveal=reveal):
                branch = self.replay(game, load=True)
                if reveal:
                    before = authoritative_state_hash(branch.state)
                    there = {"action_id": "turn-face-up:" + source.ref, "pay": "auto"}
                    self.assertFalse(branch.act("pilot:B", there).ok)
                    self.assertEqual(before, authoritative_state_hash(branch.state))
                    up = branch.act("pilot:A", there)
                    self.assertTrue(up.ok, up.summary)
                    self.assertFalse(branch.state.cards[source.object_id].face_down)
                self.resolve_top(branch)
                self.assertEqual(reveal, branch.state.players["A"].in_game)
                if reveal:
                    self.assertEqual(1, branch.state.players["A"].life)
                branch = self.replay(branch, load=True)

    def test_actual_life_floor_uses_the_same_batch_as_immediate_prevention_life_gain(self):
        from test_mixed_prevention_aftermath_native import MixedPreventionAftermathNativeTests

        game = self.session(120400840)
        source = self.add(game.engine, ALI)
        self.assert_closed(game, source)
        game.state.players["A"].life = 2
        game, spell_id = MixedPreventionAftermathNativeTests.prepare_prevented_spell(
            self, game, "Earthquake", {"C": 5, "R": 1}, x=5, reflect=False,
        )
        self.resolve_top(game)
        self.assertEqual([3, 35, 35, 35], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("graveyard", game.state.cards[spell_id].zone)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)
