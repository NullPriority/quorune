"""Native evidence for the two printed postcombat phase grants."""

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from quorune.additional_beginning_model import ADDITIONAL_BEGINNING_CAPABILITY, AdditionalBeginningPhases
from quorune.attachments import current_attached_player_seat
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.rules.additional_beginning import additional_beginning_runtime_dependencies
from quorune.turn_step_owner import TurnStepOwner
from quorune.drawing.step_exemption import is_first_draw_in_own_draw_step


class AdditionalBeginningPhasesTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn
    step = OptionalResultFollowupFixture.step

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "extra-beginning.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/draw-step-exempt-multiplier-cards.json",
            ROOT / "tests/fixtures/additional-beginning-originals-cards.json",
            ROOT / "tests/fixtures/damage-result-cards.json",
        ], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Additional beginning", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_hive_upkeep_resolves_life_then_mite_and_replays(self):
        game = self.session(1840001)
        hive = self.add(game.engine, "Skrelv's Hive")
        self.assert_closed(game, hive)
        self.step(game, "B", "beginning", "upkeep")
        self.assertFalse(game.state.stack)
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.step(game, "A", "beginning", "upkeep")
        self.assertEqual(1, self.trigger_count(game, hive))
        self.assertEqual(40, game.state.players["A"].life)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["A"].life)
        mite, = [card for card in game.state.cards.values() if card.is_token]
        self.assertEqual(("battlefield", "A"), (mite.zone, mite.controller))
        effective = game.engine._effective_card_data(mite)
        self.assertEqual("Phyrexian Mite Token", effective["name"])
        self.assertEqual(("1", "1"), tuple(str(effective[k]) for k in ("power", "toughness")))
        self.assertEqual([], list(effective["colors"]))
        self.assertIn("artifact", effective["type_line"].casefold())
        self.assertIn("creature", effective["type_line"].casefold())
        fragments = {fragment["kind"]: fragment["value"] for fragment in effective["ability_fragments"]}
        self.assertEqual(1, fragments["toxic"]["value"])
        restriction = fragments["declaration_restriction"]
        self.assertEqual(("self", "prohibit", ["block"]), (
            restriction["scope"], restriction["mode"], restriction["declarations"],
        ))
        self.replay(game, load=True)

    def test_hive_corrupted_uses_any_opponent_and_actual_toxic_damage_replays(self):
        for poison in (2, 3):
            with self.subTest(poison=poison):
                game = self.session(1840010 + poison)
                hive = self.add(game.engine, "Skrelv's Hive")
                chorus = self.add(game.engine, "Crawling Chorus")
                plain = self.add(game.engine, "Perilous Myr")
                opposing = self.add(game.engine, "Crawling Chorus", seat="B", ref="opposing-chorus")
                self.assert_closed(game, hive)
                chorus.acquired_control_turn_count = -1
                plain.acquired_control_turn_count = -1
                game.state.players["A"].poison = 3
                game.state.players["D"].poison = poison
                self.prepare(game)
                self.checkpoint(game)
                for creature, has_lifelink in ((chorus, poison == 3), (plain, False), (opposing, False)):
                    keywords = game.engine._effective_card_data(creature)["keywords"]
                    self.assertEqual(has_lifelink, "lifelink" in {k.casefold() for k in keywords})
                self.attack(game, [(chorus, "C"), (plain, "C")])
                self.until(game, lambda s: s.state.phase == "postcombat_main")
                self.assertEqual(40 + int(poison == 3), game.state.players["A"].life)
                self.assertEqual(38, game.state.players["C"].life)
                self.assertEqual(1, game.state.players["C"].poison)
                self.assertEqual(poison, game.state.players["D"].poison)
                self.replay(game, load=True)

    def test_multiple_actual_beginning_sequences_use_the_current_turn_and_end_after_the_draw(self):
        game = self.session(50008001)
        self.prepare(game)
        self.until(game, lambda current: current.state.phase == "postcombat_main"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")
        turn = game.state.turn_sequence
        active = game.state.active_player
        for _ in range(2):
            game.engine.turn_steps.schedule_additional_beginning(active, source="fixture-phase-grant")
        self.checkpoint(game)
        for pending in (1, 0):
            self.until(game, lambda current: (current.state.phase, current.state.step) == ("beginning", "draw")
                       and current.state.additional_beginning_phases == AdditionalBeginningPhases(pending, True, 1)
                       and current.state.pending_decision is not None
                       and current.state.pending_decision.kind == "priority")
            self.assertEqual(turn, game.state.turn_sequence)
            self.assertFalse(is_first_draw_in_own_draw_step(game.state, active))
            game = self.replay(game, load=True)
        self.until(game, lambda current: (current.state.phase, current.state.step) == ("ending", "end_step"))
        self.assertIsNone(game.state.additional_beginning_phases)
        self.assertEqual(turn, game.state.turn_sequence)
        self.replay(game, load=True)

    def test_additional_beginning_reader_requires_a_strict_active_count_and_round_trips(self):
        for value in (AdditionalBeginningPhases(1), AdditionalBeginningPhases(0, True, 2)):
            self.assertEqual(value, AdditionalBeginningPhases.from_dict(value.to_dict()))
        for value in ({"pending": True, "active": False, "draws_completed": 0},
                      {"pending": 1, "active": False, "draws_completed": 1},
                      {"pending": 0, "active": False, "draws_completed": 0}):
            with self.assertRaises(ValueError):
                AdditionalBeginningPhases.from_dict(value)

    def test_each_extra_draw_step_has_its_own_first_actual_draw_exemption(self):
        game = self.session(50008002)
        insight = self.add(game.engine, "Teferi's Ageless Insight")
        mists = self.add(game.engine, "Reach Through Mists", zone="hand", ref="extra-step-later-draw")
        self.add(game.engine, "Island")
        self.assert_closed(game, insight)
        self.assert_closed(game, mists)
        self.prepare(game)
        self.until(game, lambda current: current.state.phase == "postcombat_main"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")
        active = game.state.active_player
        for _ in range(2):
            game.engine.turn_steps.schedule_additional_beginning(active, source="fixture-phase-grant")
        self.checkpoint(game)
        self.until(game, lambda current: current.state.additional_beginning_phases == AdditionalBeginningPhases(1, True, 1)
                   and current.state.step == "draw" and current.state.pending_decision.kind == "priority")
        self.cast_for(game, active, mists)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(AdditionalBeginningPhases(1, True, 3), game.state.additional_beginning_phases)
        self.until(game, lambda current: current.state.additional_beginning_phases == AdditionalBeginningPhases(0, True, 1)
                   and current.state.step == "draw" and current.state.pending_decision.kind == "priority")
        self.replay(game, load=True)


    def reach_postcombat(self, game, player):
        self.until(game, lambda current: current.state.active_player == player
                   and (current.state.phase, current.state.step) == ("postcombat_main", "main")
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")

    def reach_added_draw(self, game, player, turn):
        self.until(game, lambda current: current.state.additional_beginning_phases == AdditionalBeginningPhases(0, True, 1)
                   and (current.state.phase, current.state.step) == ("beginning", "draw")
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")
        self.assertEqual((player, turn), (game.state.active_player, game.state.turn_sequence))

    def resolve_sphinx(self, game):
        self.reach_postcombat(game, "A")
        self.assertEqual(1, len(game.state.stack))
        self.assertEqual("A", game.state.stack[-1].controller)
        self.assertIsNone(game.state.additional_beginning_phases)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(
            AdditionalBeginningPhases(1), game.state.additional_beginning_phases,
            "The resolved Sphinx trigger must schedule one beginning phase",
        )

    def test_actual_sphinx_adds_a_beginning_only_to_its_controllers_turn(self):
        game = self.session(50008003)
        sphinx = self.add(game.engine, "Sphinx of the Second Sun")
        self.assert_closed(game, sphinx)
        self.prepare(game)
        self.checkpoint(game)
        self.resolve_sphinx(game)
        turn = game.state.turn_sequence
        game = self.replay(game, load=True)
        self.reach_added_draw(game, "A", turn)
        self.finish_turn(game)
        self.reach_postcombat(game, "B")
        self.assertEqual(0, self.trigger_count(game, sphinx))
        self.assertIsNone(game.state.additional_beginning_phases)
        self.replay(game, load=True)

    def test_actual_shadow_grants_the_enchanted_players_phase_after_the_aura_leaves(self):
        game = self.session(50008004)
        shadow = self.add(game.engine, "Shadow of the Second Sun", zone="hand")
        remove = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B", ref="shadow-white-one")
        self.add(game.engine, "Plains", seat="B", ref="shadow-white-two")
        private = self.add(game.engine, "Reach Through Mists", seat="D", zone="hand")
        self.assert_closed(game, shadow)
        self.prepare(game, mana={"U": 2, "C": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", shadow, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", current_attached_player_seat(game.state.cards[shadow.object_id], game.state.players))
        self.reach_postcombat(game, "A")
        self.assertEqual(0, self.trigger_count(game, shadow))
        self.assertIsNone(game.state.additional_beginning_phases)
        self.finish_turn(game)
        self.reach_postcombat(game, "B")
        self.assertEqual(1, self.trigger_count(game, shadow))
        self.assertEqual("A", game.state.stack[-1].controller)
        turn = game.state.turn_sequence
        self.cast_for(game, "B", remove, [shadow.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[shadow.object_id].zone)
        self.assertEqual(AdditionalBeginningPhases(1), game.state.additional_beginning_phases)
        self.reach_added_draw(game, "B", turn)
        for observer in ("A", "B", "C"):
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + observer, full=True)))
        self.replay(game, load=True)

    def test_additional_beginning_requires_the_capability_and_exact_captured_player(self):
        game = self.session(50008005)
        source = self.add(game.engine, "Sphinx of the Second Sun")
        program = next(
            program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
            if ADDITIONAL_BEGINNING_CAPABILITY in program.capability_dependencies
        )
        dependencies = tuple(
            dependency for dependency in program.capability_dependencies
            if dependency != ADDITIONAL_BEGINNING_CAPABILITY
        )
        changed = replace(
            program, capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict(),
        )
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + ADDITIONAL_BEGINNING_CAPABILITY, result['blockers'])
        for alternate in (
            replace(program, effects=(dict(program.effects[0], player="B"),)),
            replace(program, effects=(dict(program.effects[0], schema_version=True),)),
            replace(program, event_condition={"field": "phase", "op": "eq", "value": "precombat_main"}),
        ):
            with self.assertRaises(ValueError):
                additional_beginning_runtime_dependencies(alternate)

    def test_native_sphinx_contract_detects_an_omitted_phase_grant(self):
        game = self.session(50008006)
        self.add(game.engine, "Sphinx of the Second Sun")
        self.prepare(game)
        with patch.object(TurnStepOwner, "schedule_additional_beginning", return_value=None):
            with self.assertRaisesRegex(AssertionError, "resolved Sphinx trigger"):
                self.resolve_sphinx(game)
