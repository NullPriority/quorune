from __future__ import annotations

import unittest

from common import keep_all, load_assets, make_session
from quorune.errors import GameRuleError, StateInvariantError
from quorune.record import authoritative_state_hash
from quorune.turn_priority_model import PriorityGrantPlan, PriorityPassPlan
from quorune.turn_step_owner import TURN_STEPS


class TurnPriorityDecisionOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_engine(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        return engine

    def test_priority_plans_are_closed_immutable_and_canonical(self):
        grant = PriorityGrantPlan("A", 4, cleanup_frame=False)
        self.assertEqual(grant, PriorityGrantPlan.from_dict(grant.to_dict()))
        with self.assertRaisesRegex(ValueError, "invalid shape"):
            PriorityGrantPlan.from_dict({**grant.to_dict(), "extra": True})
        with self.assertRaisesRegex(ValueError, "exact integer"):
            PriorityGrantPlan("A", True)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "nonempty string"):
            PriorityGrantPlan.from_dict(
                {"seat": 1, "priority_epoch": 4, "cleanup_frame": False}
            )

        passed = PriorityPassPlan(
            seat="A",
            passes=("A",),
            next_seat="B",
            round_complete=False,
            stack_waiting=False,
        )
        self.assertEqual(passed, PriorityPassPlan.from_dict(passed.to_dict()))
        with self.assertRaisesRegex(ValueError, "unique"):
            PriorityPassPlan(
                seat="A",
                passes=("A", "A"),
                next_seat="B",
                round_complete=False,
                stack_waiting=False,
            )
        with self.assertRaisesRegex(ValueError, "cannot name a next seat"):
            PriorityPassPlan(
                seat="A",
                passes=("A", "B"),
                next_seat="A",
                round_complete=True,
                stack_waiting=False,
            )
        with self.assertRaisesRegex(ValueError, "nonempty string"):
            PriorityPassPlan.from_dict(
                {
                    "seat": "A",
                    "passes": ["A"],
                    "next_seat": 2,
                    "round_complete": False,
                    "stack_waiting": False,
                }
            )

    def test_engine_facade_commits_through_typed_priority_owner(self):
        engine = self.make_engine(5001001)
        owner = engine.turn_priority
        plan = owner.prepare_priority_grant("A", cleanup_frame=False)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(engine.state.priority_epoch + 1, plan.priority_epoch)

        owner.commit_priority_grant(plan)

        self.assertEqual("A", engine.state.priority_player)
        self.assertEqual([], engine.state.priority_passes)
        self.assertEqual(plan.priority_epoch, engine.state.priority_epoch)
        with self.assertRaisesRegex(StateInvariantError, "stale"):
            owner.commit_priority_grant(plan)

    def test_priority_pass_plan_preserves_turn_order_and_rejects_wrong_actor(self):
        engine = self.make_engine(5001002)
        engine._grant_priority("A")

        with self.assertRaisesRegex(GameRuleError, "does not have priority"):
            engine.turn_priority.prepare_priority_pass("B")
        plan = engine.turn_priority.prepare_priority_pass("A")
        self.assertEqual(("A",), plan.passes)
        self.assertEqual("B", plan.next_seat)
        self.assertFalse(plan.round_complete)

        engine.turn_priority.commit_priority_pass(plan, automatic=True)
        self.assertEqual("B", engine.state.priority_player)
        self.assertEqual(["A"], engine.state.priority_passes)

    def test_yield_epochs_retain_game_record_v3_keys_and_invalidate(self):
        engine = self.make_engine(5001003)
        engine._grant_priority("A")
        engine._set_yield("A", "until_public_change")
        before = engine._yield_change_epoch("stack")

        engine._log(
            "A",
            "stack.cast",
            "A stack object changed the priority window.",
            importance=0,
        )

        self.assertEqual(before + 1, engine._yield_change_epoch("stack"))
        self.assertIn("yield_change:stack", engine.state.ref_counters)
        self.assertEqual("stack", engine._yield_stop_reason("A"))

    def test_owner_mutation_rolls_back_and_remains_bound_to_live_state(self):
        engine = self.make_engine(5001004)
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "rollback witness"):
            with engine.transaction():
                engine._grant_priority("A")
                raise GameRuleError("rollback witness")

        self.assertEqual(before, authoritative_state_hash(engine.state))
        engine._grant_priority("A")
        self.assertEqual("A", engine.state.priority_player)

    def test_priority_decision_payload_has_stable_yield_modes(self):
        engine = self.make_engine(5001005)
        engine._grant_priority("A")
        decision = engine._issue_priority(
            "A",
            {
                "actions": [{"id": "pass", "kind": "pass"}],
                "cast": [],
                "lands": [],
                "abilities": [],
                "mana_abilities": [],
            },
        )

        self.assertEqual(
            [
                "none",
                "until_public_change",
                "until_my_turn",
                "auto_if_no_response",
            ],
            decision.payload_by_actor["A"]["yield_modes"],
        )

    def test_turn_owner_selects_extra_turns_lifo_then_normal_order(self):
        engine = self.make_engine(5001006)
        engine.schedule_extra_turn("B", source="first")
        engine.schedule_extra_turn("A", source="second")

        self.assertEqual("A", engine._select_next_turn().player)
        self.assertEqual("B", engine._select_next_turn().player)
        self.assertEqual("B", engine._select_next_turn().player)

    def test_turn_owner_rejects_invalid_step_index_before_mutation(self):
        engine = self.make_engine(5001007)
        engine.state.phase_index = len(TURN_STEPS)
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(StateInvariantError, "turn-step index"):
            engine._enter_step()

        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_turn_owner_step_entry_sets_boundary_before_callback(self):
        engine = self.make_engine(5001008)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.phase_index = TURN_STEPS.index(
            ("precombat_main", "main")
        )
        engine.state.phase = "beginning"
        engine.state.step = "draw"

        engine._enter_step()

        self.assertEqual("precombat_main", engine.state.phase)
        self.assertEqual("main", engine.state.step)
        self.assertTrue(
            any(
                event.code == "step.begin"
                and event.summary.endswith("precombat_main/main.")
                for event in engine.state.events
            )
        )

    def test_departed_active_player_turn_continues_through_step_boundary(self):
        engine = self.make_engine(5001009)
        self.assertEqual("A", engine.state.active_player)
        engine.state.players["A"].in_game = False
        engine.state.phase_index = TURN_STEPS.index(
            ("postcombat_main", "main")
        )

        engine._enter_step()

        self.assertEqual("A", engine.state.active_player)
        self.assertEqual("postcombat_main", engine.state.phase)
        self.assertEqual("main", engine.state.step)
        self.assertEqual("B", engine.state.priority_player)


class ConcessionPriorityRoundRuntimeTests(unittest.TestCase):
    """CR 104.3a/117.4/800.4a: concession interrupts successive passes.

    After the priority holder concedes, the next remaining player receives
    a usable priority window over the changed game. A pass from that player
    cannot collide with their pass before the concession. Pending records
    retain the same controller, targets, principal privacy and exact replay.
    """

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        import tempfile
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "concession-priority.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Concession priority", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 10),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    replay = _bound.BoundEffectProgramRuntimeTests.replay

    def test_actual_last_seat_concession_allows_wrapped_priority_pass_and_replays(self):
        session = self.session(343001)
        source = self.add(session.engine, "Generic Bound Draw Loss", zone="hand")
        action = self.ready(session, source, {"B": 1})
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": ["A"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        hand_before = len(session.state.players["A"].zones["hand"])
        for seat in "ABC":
            self.assertTrue(session.act("pilot:" + seat, {"action_id": "pass"}).ok)
        before = authoritative_state_hash(session.state)
        malformed = session.act("pilot:D", {"action_id": "concede"})
        self.assertFalse(malformed.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        unauthorized = session.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertFalse(unauthorized.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session, load=True)
        conceded = session.act("pilot:D", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        self.assertFalse(session.state.players["D"].in_game)
        self.assertEqual("A", session.state.priority_player)
        self.assertEqual(1, len(session.state.stack))
        self.assertEqual("A", session.state.stack[-1].controller)
        self.assertEqual(["A"], session.state.stack[-1].targets)
        for seat in "BCD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        self.replay(session, load=True)
        passed = session.act("pilot:A", {"action_id": "pass"})
        self.assertTrue(passed.ok, passed.summary)
        self.assertEqual(1, len(session.state.stack))
        self.resolve(session)
        self.assertEqual(38, session.state.players["A"].life)
        self.assertEqual(hand_before + 2, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        for seat in "BC":
            self.assertEqual(40, session.state.players[seat].life)
            public = session.packet("pilot:" + seat, full=True)["state"]["players"]["A"]
            self.assertEqual(hand_before + 2, public["hand_n"])
            self.assertNotIn("hand", public)
        self.replay(session, load=True)

    def test_concession_pass_reset_omission_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.turn_priority_owner import TurnPriorityDecisionOwner

        original = TurnPriorityDecisionOwner.complete_priority

        def omit_reset(owner, decision):
            previous = tuple(owner.state.priority_passes)
            original(owner, decision)
            if decision.responses[decision.actors[0]].get("action") == "concede":
                owner.state.priority_passes = [seat for seat in previous if seat in owner.host.active_seats]

        with patch.object(TurnPriorityDecisionOwner, "complete_priority", new=omit_reset):
            with self.assertRaisesRegex(StateInvariantError, "duplicate seats"):
                self.test_actual_last_seat_concession_allows_wrapped_priority_pass_and_replays()


if __name__ == "__main__":
    unittest.main()
