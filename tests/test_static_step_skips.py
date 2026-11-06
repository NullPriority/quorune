from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.untap_step_templates import static_turn_step_skip_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.untap_steps import STATIC_STEP_SKIP_CAPABILITY, StaticTurnStepSkipHandler
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


class StaticStepSkipCompilerTests(unittest.TestCase):
    def test_closed_step_scope_and_hellbent_descriptors(self):
        handler = StaticTurnStepSkipHandler()
        for text, step, scope, empty in (
            ("Skip your draw step.", "draw", "source_controller", False),
            ("Players skip their untap steps.", "untap", "all_players", False),
            ("Each player skips their untap step.", "untap", "all_players", False),
            ("Players skip their upkeep steps.", "upkeep", "all_players", False),
            ("Hellbent — Skip your upkeep step if you have no cards in hand.", "upkeep", "source_controller", True),
        ):
            with self.subTest(text=text):
                _, descriptor, capability = static_turn_step_skip_handler(text)
                value = handler.validate(descriptor)
                self.assertEqual((step, scope, empty), (value.step, value.scope, value.requires_empty_hand))
                self.assertEqual(STATIC_STEP_SKIP_CAPABILITY, capability)
        for text in (
            "Target player skips their next draw step.",
            "Skip your next untap step.",
            "If you would draw a card, skip that draw instead.",
            "Skip your draw step if you have no cards in hand.",
            "Players skip their cleanup steps.",
        ):
            self.assertIsNone(static_turn_step_skip_handler(text))
        _, descriptor, _ = static_turn_step_skip_handler("Skip your draw step.")
        for changed in ({"schema_version": True}, {"step": "cleanup"}, {"scope": "target_player"},
                        {"requires_empty_hand": 1}, {"requires_empty_hand": True}, {"player": "A"}):
            with self.subTest(changed=changed), self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor, **changed})


class StaticStepSkipNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "static-step-skips.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/static-step-skip-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Static step skips", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 32),
        ], ["Generic Bound Commander"])

    def end_step_before(self, session, seat):
        previous = session.state.turn_order[session.state.turn_order.index(seat) - 1]
        self.prepare(session, actor=previous, active=previous)
        state = session.state
        state.last_normal_turn_player = previous
        if state.current_turn is not None:
            state.current_turn = replace(state.current_turn, player=previous)
        state.phase, state.step = "ending", "end_step"
        state.phase_index = TURN_STEPS.index((state.phase, state.step))
        session.engine.permissions.invalidate_current()
        state.pending_decision = None
        session.engine._grant_priority(previous)
        session.engine.pump()
        self.checkpoint(session)

    def first_main(self, session, seat):
        self.until(session, lambda s: (s.state.active_player, s.state.phase) == (seat, "precombat_main"))

    def began(self, session, step):
        return any(
            event.code == "step.begin" and event.turn_sequence == session.state.turn_sequence
            and event.phase == "beginning" and event.step == step
            for event in session.state.events
        )

    def test_actual_draw_and_global_upkeep_skips_retain_held_triggers_and_other_seats_draw(self):
        for seat in "AB":
            with self.subTest(seat=seat):
                session = self.session(719001)
                session.state.config.semantic_policy = "trusted_only"
                sovereign = self.add(session.engine, "Infernal Sovereign")
                hub = self.add(session.engine, "Eon Hub", seat="B")
                for card in (sovereign, hub):
                    self.assert_closed(session, card)
                wake = self.add(session.engine, "Wake Thrasher")
                land = self.add(session.engine, "Mountain")
                land.tapped = True
                self.assert_closed(session, wake)
                self.end_step_before(session, seat)
                hand = len(session.state.players[seat].zones["hand"])
                self.first_main(session, seat)
                self.assertFalse(self.began(session, "upkeep"))
                self.assertEqual(seat == "B", self.began(session, "draw"))
                self.assertEqual(hand + int(seat == "B"), len(session.state.players[seat].zones["hand"]))
                if seat == "A":
                    self.assertEqual([wake.object_id], [item.source_object_id for item in session.state.stack])
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual(2, session.engine._numeric_stat(wake.object_id, "power"))
                self.replay(session, load=True)

    def test_actual_stasis_skips_untap_until_its_upkeep_payment_is_declined(self):
        session = self.session(719002)
        session.state.config.semantic_policy = "trusted_only"
        stasis = self.add(session.engine, "Stasis")
        lands = {seat: self.add(session.engine, "Mountain", seat=seat, ref=f"stasis-{seat}")
                 for seat in "AB"}
        for land in lands.values():
            land.tapped = True
        for object_id in tuple(session.state.players["A"].zones["hand"][5:]):
            session.engine.move_card(object_id, "library", log=False)
        self.assert_closed(session, stasis)
        program = next(item for item in session.engine.semantics.programs_for_oracle(stasis.oracle_id)
                       if item.event == "turn.step.skip")
        self.assertEqual([STATIC_STEP_SKIP_CAPABILITY], list(program.capability_dependencies))
        missing = replace(program, capability_dependencies=[], capability_closure=None)
        binding = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile="commander_review")
        self.assertIn(f"capability:undeclared_runtime_dependency:{STATIC_STEP_SKIP_CAPABILITY}", binding["blockers"])
        self.end_step_before(session, "A")

        self.until(session, lambda s: s.state.pending_decision.kind == "semantic.choice")
        self.assertFalse(self.began(session, "untap"))
        self.assertTrue(self.began(session, "upkeep"))
        self.assertTrue(session.state.cards[lands["A"].object_id].tapped)
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[stasis.object_id].zone)
        self.first_main(session, "B")
        self.assertTrue(self.began(session, "untap"))
        self.assertFalse(session.state.cards[lands["B"].object_id].tapped)
        self.replay(session, load=True)

    def test_actual_hellbent_checks_current_hand_before_the_upkeep(self):
        for empty in (True, False):
            with self.subTest(empty=empty):
                session = self.session(719003)
                session.state.config.semantic_policy = "trusted_only"
                source = self.add(session.engine, "Gibbering Descent")
                self.assert_closed(session, source)
                for object_id in tuple(session.state.players["A"].zones["hand"]):
                    session.engine.move_card(object_id, "library", log=False)
                private = None if empty else self.add(session.engine, "Generic Bound Body", zone="hand")
                self.end_step_before(session, "A")
                if empty:
                    self.first_main(session, "A")
                    self.assertFalse(self.began(session, "upkeep"))
                    self.assertEqual(40, session.state.players["A"].life)
                else:
                    self.until(session, lambda s: any(item.source_object_id == source.object_id
                                                      for item in s.state.stack))
                    self.assertTrue(self.began(session, "upkeep"))
                    self.assertIsNotNone(self.resolve(session))
                    self.assertEqual(["pilot:A"], list(session.pending_principals()))
                    self.assertNotIn(private.ref, json.dumps(session.packet("pilot:D", full=True)))
                    command = {"action_id": "choose", "cards": [private.ref]}
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act("pilot:B", command).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    result = session.act("pilot:A", command)
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual(39, session.state.players["A"].life)
                    self.assertEqual("graveyard", session.state.cards[private.object_id].zone)
                self.replay(session, load=True)

    def test_scheduler_omission_mutant_is_killed(self):
        with patch("quorune.turn_step_owner.current_step_is_skipped", return_value=False):
            with self.assertRaises(AssertionError):
                self.test_actual_stasis_skips_untap_until_its_upkeep_payment_is_declined()
