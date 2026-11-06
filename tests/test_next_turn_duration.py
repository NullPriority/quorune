"""CR 109.5, 611.2a, 614.10a, and 800.4m controller-relative expiry."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_qualified_public_history as history
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.continuous_effect_model import (
    ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin,
    ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.deck import DeckDefinition, DeckEntry
from quorune.declaration_rule_effects import ResolutionDeclarationRuleEffect
from quorune.engine import CommanderEngine
from quorune.model import GameConfig, GameState, PlayerState, TurnEntry
from quorune.next_turn_effect import (
    NEXT_TURN_EFFECT_CAPABILITY, NEXT_TURN_EFFECT_FIELD, next_turn_effect_dependencies,
)
from quorune.oracle_ir import _reviewed_effect_template
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.node_capability_shapes import temporary_declaration_restriction_node_capabilities
from quorune.rules.target_characteristic_sets import target_characteristic_set_capabilities
from quorune.rules.temporary_declaration_restrictions import temporary_declaration_restriction
from quorune.turn_skip_model import TurnBoundary, TurnSkipSpec
from quorune.turn_step_owner import TurnStepOwner
from scripts.build_test_database import build_fixture_database


class TurnBoundaryHost:
    _next_active_after = CommanderEngine._next_active_after

    def __init__(self):
        self.state = GameState(
            game_id="turn-expiry", config=GameConfig(), players={seat: PlayerState(seat, seat) for seat in "ABCD"},
            cards={}, deck_names={}, commander_oracle_ids={}, turn_order=list("ABCD"),
            current_turn=TurnEntry("N1", "A", False, created_sequence=0),
            last_normal_turn_player="A", active_player="A", turn_sequence=1,
        )
        self._log = Mock()
        self._expire_goad_designations = Mock()
        self.next_reference = 1

    @property
    def active_seats(self):
        return [seat for seat in self.state.turn_order if self.state.players[seat].in_game]

    def _next_ref(self, prefix):
        self.next_reference += 1
        return f"{prefix}{self.next_reference}"

    def _require_seat(self, seat, *, in_game):
        assert seat in self.active_seats and in_game


class NextTurnJournalTests(unittest.TestCase):
    def setUp(self):
        self.host = TurnBoundaryHost()
        self.state = self.host.state
        self.owner = TurnStepOwner(self.host)
        locked = (ContinuousObjectIdentity("object", "incarnation"),)
        expiry = dict(duration=ContinuousEffectDuration.UNTIL_NEXT_TURN, expires_on_next_turn_of="B")
        self.stats = ContinuousEffect(
            effect_id="CE1", source_id="S1", layer=Layer.POWER_TOUGHNESS, sublayer="7c", timestamp=1,
            operations=(ContinuousOperation("modify_power_toughness", (-4, 0)),),
            origin=ContinuousEffectOrigin.RESOLUTION, locked_objects=locked, **expiry,
        )
        self.rule = ResolutionDeclarationRuleEffect(
            effect_id="DR1", source_id="S1", timestamp=1, locked_objects=locked,
            restriction=temporary_declaration_restriction("cant_attack"), **expiry,
        )
        for effect in (self.stats, self.rule):
            commit_continuous_effect(self.state, effect)

    def begin(self, seat, *, extra=False):
        with patch.object(self.owner, "enter_step"):
            self.owner.begin_turn(self.owner.select_next_turn())
        self.assertEqual((seat, extra), (self.state.current_turn.player, self.state.current_turn.extra))

    def depart_b(self):
        self.state.players["B"].in_game = False
        self.state.eliminated_players.append("B")
        self.owner.retire_departed_turns("B")

    def test_living_anchor_waits_past_a_skipped_extra_turn(self):
        self.owner.schedule_extra_turn("C")
        self.owner.schedule_extra_turn("B")
        self.owner.schedule_skip("B", TurnSkipSpec(TurnBoundary.TURN))
        self.begin("C", extra=True)
        self.assertEqual([self.stats, self.rule], self.state.continuous_effects)
        self.assertEqual(0, self.state.players["B"].turns_begun)
        self.begin("B")
        self.assertEqual([], self.state.continuous_effects)

    def test_departed_anchor_expires_at_its_first_eligible_slot(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                self.setUp()
                self.owner.schedule_extra_turn("C")
                self.owner.schedule_extra_turn("B")
                if skipped:
                    self.owner.schedule_skip("B", TurnSkipSpec(TurnBoundary.TURN))
                self.depart_b()
                self.assertEqual([self.stats, self.rule], self.state.continuous_effects)
                self.begin("C", extra=True)
                self.assertEqual([self.stats, self.rule] if skipped else [], self.state.continuous_effects)
                self.begin("C")
                self.assertEqual([], self.state.continuous_effects)
                self.assertEqual(0, self.state.players["B"].turns_begun)
                self.assertFalse(any(call.args[:2] == ("B", "turn.begin") for call in self.host._log.call_args_list))

    def test_omitting_expiry_is_detected_at_the_departed_extra_boundary(self):
        self.owner.schedule_extra_turn("C")
        self.owner.schedule_extra_turn("B")
        self.depart_b()
        with patch("quorune.turn_step_owner.expire_next_turn_continuous_effects"):
            self.begin("C", extra=True)
        with self.assertRaises(AssertionError):
            self.assertEqual([], self.state.continuous_effects)

    def test_journal_round_trips_and_rejects_missing_or_unknown_anchors(self):
        wire = self.state.to_dict()
        self.assertEqual(wire, GameState.from_dict(wire).to_dict())
        for index in range(2):
            for bad in (None, "unknown", True):
                changed = deepcopy(wire)
                row = changed["continuous_effects"][index]
                if bad is None:
                    del row["expires_on_next_turn_of"]
                else:
                    row["expires_on_next_turn_of"] = bad
                with self.subTest(effect=index, anchor=bad), self.assertRaises(ValueError):
                    GameState.from_dict(changed)
        legacy = deepcopy(wire)
        del legacy["continuous_effects"]
        self.assertEqual(legacy, GameState.from_dict(legacy).to_dict())


class NextTurnCompilerTests(unittest.TestCase):
    def test_room_bodies_share_existing_target_and_result_authority(self):
        cases = (
            ("Target creature gets -4/-0 until your next turn.", "apply_source_characteristics_until_end_of_turn",
             target_characteristic_set_capabilities),
            ("Target creature can't attack until your next turn.", "grant_declaration_restriction_until_end_of_turn",
             temporary_declaration_restriction_node_capabilities),
        )
        for text, operation, dependencies in cases:
            with self.subTest(text=text):
                _, effects, targets, mechanics = _reviewed_effect_template(
                    text, card_name="Dungeon room", source_is_permanent=False,
                )
                effect, = effects
                self.assertEqual(operation, effect["op"])
                self.assertIs(effect[NEXT_TURN_EFFECT_FIELD], True)
                self.assertEqual(["creature"], targets["types_any"])
                self.assertEqual(1, targets["count"])
                self.assertEqual((NEXT_TURN_EFFECT_CAPABILITY,), next_turn_effect_dependencies(effects))
                self.assertIn(NEXT_TURN_EFFECT_CAPABILITY, dependencies(
                    effects=effects, target_schema=targets, mechanic_ids=mechanics,
                ))

    def test_mixed_deadlines_and_unrepresented_results_fail_closed(self):
        for text in (
            "Target creature gets -4/-0 until its controller's next turn.",
            "Until your next turn, target creature gets -4/-0 until end of turn.",
            "Target creature gets -4/-0 until your next turn and performs a ritual.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(_reviewed_effect_template(text, card_name="Dungeon room", source_is_permanent=False)[0])
        for malformed in ({"op": "draw", NEXT_TURN_EFFECT_FIELD: True},
                          {"op": "apply_source_characteristics_until_end_of_turn", "schema_version": 6,
                           NEXT_TURN_EFFECT_FIELD: 1}):
            with self.assertRaises(ValueError):
                next_turn_effect_dependencies({"effects": [malformed]})


class NextTurnNativeTests(InheritedNativeFixture, unittest.TestCase):
    until = history.QualifiedPublicHistoryNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "next-turn.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json", fixtures / "fixed-extra-turn-cards.json",
            fixtures / "next-turn-duration-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Next turn", [DeckEntry("Generic Bound Commander", 1, "commander"),
                                               DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    def test_next_turn_dependency_cannot_be_removed_from_an_original_program(self):
        session = self.session(61128003)
        mass = self.add(session.engine, "Mass Diminish", zone="hand")
        self.assert_closed(session, mass)
        program = next(p for p in session.engine.semantics.programs_for_oracle(mass.oracle_id)
                       if NEXT_TURN_EFFECT_CAPABILITY in p.capability_dependencies)
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != NEXT_TURN_EFFECT_CAPABILITY)
        altered = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{NEXT_TURN_EFFECT_CAPABILITY}", binding["blockers"])

    def test_actual_mass_diminish_expires_before_the_surviving_extra_turn(self):
        session = self.session(61128004)
        mass = self.add(session.engine, "Mass Diminish", zone="hand")
        warp_c = self.add(session.engine, "Time Warp", zone="hand", ref="warp-for-c")
        warp_a = self.add(session.engine, "Time Warp", zone="hand", ref="warp-for-a")
        bear = self.add(session.engine, "Grizzly Bears", seat="C")
        viper = self.add(session.engine, "Ambush Viper", seat="C", zone="hand")
        for card in (mass, warp_c, viper):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 7, "U": 5})
        session.state.players["C"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(session)

        before = authoritative_state_hash(session.state)
        action = self.action(session, "A", mass)
        bad = session.act("pilot:B", {"action_id": action["id"], "targets": ["C"], "pay": "auto"})
        self.assertFalse(bad.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, "A", mass, ["C"])
        self.resolve_top(session)
        self.assertEqual("1", session.engine._effective_card_data(bear)["power"])
        self.cast_for(session, "C", viper)
        self.resolve_top(session)
        self.assertEqual("2", session.engine._effective_card_data(viper)["power"])
        for spell, seat in ((warp_c, "C"), (warp_a, "A")):
            self.cast_for(session, "A", spell, [seat])
            self.resolve_top(session)
        self.priority(session, "A")
        result = session.act("pilot:A", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("A", session.state.active_player)
        self.assertEqual("1", session.engine._effective_card_data(bear)["power"])
        self.assertEqual(["A", "C"], [entry.player for entry in session.state.extra_turns])
        for viewer in "BCD":
            packet = session.packet("pilot:" + viewer, full=True)
            self.assertEqual(["C"], packet["state"]["turn"]["extra_q"])
        session = self.replay(session, load=True)
        self.assertEqual("A", session.state.continuous_effects[0].expires_on_next_turn_of)
        self.until(session, lambda game: game.state.active_player == "C")
        self.assertTrue(session.state.current_turn.extra)
        self.assertEqual("2", session.engine._effective_card_data(session.state.cards[bear.object_id])["power"])
        self.assertEqual([], session.state.continuous_effects)
        self.replay(session, load=True)
