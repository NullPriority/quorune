from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import load_assets, make_session
from quorune import (
    CommandEnvelope,
    GameActorClosed,
    GameActorUnavailable,
    GameManager,
    GameService,
    PROTOCOL_VERSION,
)
from quorune.characteristic_read_frames import memoized_characteristic_read
from quorune.runtime import GameActor, GameLifecycleConflict
from quorune.turn_priority_model import PriorityWindowHolds, upcoming_turn_player
from quorune.turn_skip_model import ScheduledTurnSkip, TurnBoundary, TurnSkipSpec


class PriorityWindowScheduleTests(unittest.TestCase):
    def test_before_my_turn_respects_extra_turns_skips_and_departed_seats(self):
        state = SimpleNamespace(
            phase="ending", step="end_step", turn_sequence=21, last_normal_turn_player="D",
            turn_order=("A", "B", "C", "D"),
            players={seat: SimpleNamespace(in_game=(seat != "A")) for seat in "ABCD"},
            scheduled_turn_skips=[ScheduledTurnSkip("skip-b", "B", TurnSkipSpec(TurnBoundary.TURN))],
            extra_turns=[],
        )
        hold = PriorityWindowHolds(("before_my_turn",), False)
        self.assertEqual("C", upcoming_turn_player(state))
        self.assertTrue(hold.matches(state, "C"))
        self.assertFalse(hold.matches(state, "B"))
        state.extra_turns = [
            SimpleNamespace(player="A", skip_steps=[]),
            SimpleNamespace(player="B", skip_steps=[]),
            SimpleNamespace(player="D", skip_steps=[]),
        ]
        self.assertEqual("D", upcoming_turn_player(state))
        self.assertTrue(hold.matches(state, "D"))
        self.assertEqual(1, state.scheduled_turn_skips[0].spec.count)
        state.step = "cleanup"
        self.assertFalse(hold.matches(state, "D"))


class GameActorIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_conflicting_request_retains_receipt_and_actor_accepts_next_command(self):
        principal = "pilot:A"
        state = SimpleNamespace(game_id="idempotency-conflict", revision=0, players={"A": object()})
        calls = []

        def decision_for_revision():
            return SimpleNamespace(
                decision_id=f"decision-{state.revision}",
                kind="mulligan.declare",
                payload_by_actor={"A": {}},
            )

        def capability_for(who):
            if who != principal:
                return None
            return SimpleNamespace(
                token=f"token-{state.revision}",
                actor="A",
                allowed_actions=("keep", "mulligan"),
            )

        def act(who, response, *, client_command_id, priority_window_holds):
            calls.append((who, response["action_id"], client_command_id))
            state.revision += 1
            state.pending_decision = decision_for_revision()
            session.commands.append({"principal": who, "client_command_id": client_command_id})
            return SimpleNamespace(
                ok=True, summary="Accepted keep", state_changed=True, event_ids=[state.revision],
            )

        def envelope(command_id):
            return CommandEnvelope(
                protocol_version=PROTOCOL_VERSION, game_id=state.game_id,
                command_id=command_id, decision_id=state.pending_decision.decision_id,
                action_id="keep", capability=capability_for(principal).token,
                expected_view_revision=state.revision, choices={},
            )

        state.pending_decision = decision_for_revision()
        session = SimpleNamespace(
            state=state, record_status="in_progress", commands=[],
            engine=SimpleNamespace(permissions=SimpleNamespace(capability_for=capability_for)),
            act=act, packet=lambda who, **kwargs: {"principal": who, "view_revision": state.revision},
        )
        original = envelope("original")
        accepted = GameService(session).command(original, principal=principal)
        self.assertTrue(accepted.ok)
        service = GameService(session)
        stored = service.idempotency.get(state.game_id, principal, original.command_id)
        self.assertIsNotNone(stored)
        writes = []
        persistence = SimpleNamespace(save=lambda service: writes.append(service.session.state.revision))
        actor = GameActor(service, persistence=persistence)
        try:
            conflict = await actor.command(principal, replace(original, action_id="mulligan"))
            self.assertFalse(conflict.ok)
            self.assertEqual("idempotency_conflict", conflict.code)
            self.assertFalse(conflict.state_changed)
            self.assertFalse(actor.requires_recovery)
            self.assertIs(stored, service.idempotency.get(state.game_id, principal, original.command_id))
            self.assertEqual([], writes)
            self.assertEqual(1, len(session.commands))
            self.assertEqual(accepted.state_revision, (await actor.observe(principal))["view_revision"])

            next_receipt = await actor.command(principal, envelope("next"))
            self.assertTrue(next_receipt.ok)
            self.assertEqual(accepted.state_revision + 1, next_receipt.state_revision)
            self.assertEqual([next_receipt.state_revision], writes)
            self.assertEqual(
                [(principal, "keep", "original"), (principal, "keep", "next")], calls,
            )

            replayed = await actor.command(principal, original)
            self.assertEqual(replace(accepted, replayed=True), replayed)
            self.assertIs(stored, service.idempotency.get(state.game_id, principal, original.command_id))
            self.assertFalse(actor.requires_recovery)
            self.assertEqual(2, len(session.commands))
            self.assertEqual([next_receipt.state_revision], writes)
        finally:
            await actor.close()


class GameActorTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_service(self, seed: int):
        session = make_session(
            self.db, self.mishra, self.zimone, seed=seed
        )
        return session, GameService(session)

    @staticmethod
    def envelope(session, command_id: str = "actor-1"):
        capability = session.engine.permissions.capability_for("pilot:A")
        decision = session.state.pending_decision
        return CommandEnvelope(
            protocol_version=PROTOCOL_VERSION,
            game_id=session.state.game_id,
            command_id=command_id,
            decision_id=decision.decision_id,
            action_id="keep",
            capability=capability.token,
            expected_view_revision=session.state.revision,
            choices={},
        )

    async def test_actor_serializes_duplicate_concurrent_delivery(self):
        session, service = self.make_service(32001)
        manager = GameManager()
        actor = await manager.add(service)
        envelope = self.envelope(session)
        try:
            first, duplicate = await asyncio.gather(
                actor.command("pilot:A", envelope),
                actor.command("pilot:A", envelope),
            )
            self.assertTrue(first.ok)
            self.assertTrue(duplicate.ok)
            self.assertFalse(first.replayed)
            self.assertTrue(duplicate.replayed)
            self.assertEqual(1, len(session.commands))
            self.assertEqual(2, actor.processed_messages)
            self.assertEqual(0, actor.queue_depth)
        finally:
            await manager.close()

    async def test_only_one_actor_can_own_a_game(self):
        session, service = self.make_service(32002)
        manager = GameManager()
        actor = await manager.add(service)
        self.assertIs(actor, manager.get(session.state.game_id))
        with self.assertRaisesRegex(ValueError, "already has an actor"):
            await manager.add(service)
        await manager.close()

    async def test_observations_use_the_same_mailbox_and_actor_closes(self):
        session, service = self.make_service(32003)
        manager = GameManager()
        actor = await manager.add(service)
        packet = await actor.observe("pilot:A", full=True)
        self.assertEqual(session.state.game_id, packet["state"]["game"]["id"])
        self.assertEqual(1, actor.processed_messages)
        await manager.close()
        with self.assertRaises(GameActorClosed):
            await actor.observe("pilot:A")

    async def test_repeated_observations_reuse_only_the_same_principal_and_boundary(self):
        session, service = self.make_service(32011)
        manager = GameManager()
        actor = await manager.add(service)
        try:
            first = await actor.observe("pilot:A", full=True)
            first["state"]["rev"] = -1
            next_packet = await actor.observe("pilot:A", full=True)
            self.assertEqual(first["view"], next_packet["view"])
            self.assertEqual(session.state.revision, next_packet["view_revision"])
            self.assertNotEqual(-1, next_packet["state"]["rev"])
            self.assertEqual([], (await actor.observe("pilot:A"))["patch"])
            other = await actor.observe("pilot:B", full=True)
            self.assertFalse("hand" in other["state"]["players"]["A"])
            self.assertTrue((await actor.command("pilot:A", self.envelope(session))).ok)
            changed = await actor.observe("pilot:A", full=True)
            self.assertNotEqual(first["view"], changed["view"])
            self.assertIsNone(changed["decision"])
        finally:
            await manager.close()

    async def test_queued_observations_share_rules_reads_until_the_next_command(self):
        session, service = self.make_service(32012)
        manager = GameManager()
        actor = await manager.add(service)
        initial_revision = session.state.revision
        read_revisions = []
        original_observe = service.observe

        def read_current_revision():
            read_revisions.append(session.state.revision)
            return session.state.revision

        def observe(principal, **kwargs):
            revision = memoized_characteristic_read(
                session.engine, ("observation-batch-test",), read_current_revision,
            )
            packet = original_observe(principal, **kwargs)
            self.assertEqual(revision, packet["view_revision"])
            return packet

        try:
            with patch.object(service, "observe", side_effect=observe):
                before_a, before_b, receipt, after_a, after_b = await asyncio.gather(
                    actor.observe("pilot:A", full=True),
                    actor.observe("pilot:B", full=True),
                    actor.command("pilot:A", self.envelope(session)),
                    actor.observe("pilot:A", full=True),
                    actor.observe("pilot:B", full=True),
                )
                self.assertTrue(receipt.ok)
                self.assertGreater(receipt.state_revision, initial_revision)
                self.assertEqual([initial_revision, receipt.state_revision], read_revisions)
                independent = await actor.observe("pilot:B", full=True)
                self.assertEqual(receipt.state_revision, read_revisions[-1])
                self.assertEqual(3, len(read_revisions))

            for a_packet, b_packet in ((before_a, before_b), (after_a, after_b)):
                self.assertIn("hand", a_packet["state"]["players"]["A"])
                self.assertNotIn("hand", b_packet["state"]["players"]["A"])
                self.assertIn("hand", b_packet["state"]["players"]["B"])
                self.assertNotIn("hand", a_packet["state"]["players"]["B"])
            self.assertEqual(after_b["view"], independent["view"])
        finally:
            await manager.close()

    async def test_projector_exception_leaves_the_actor_worker_available(self):
        calls = 0

        def observe(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("Projection unavailable")
            return {"observation": calls}

        service = SimpleNamespace(session=SimpleNamespace(state=SimpleNamespace(game_id="actor-read")), observe=observe)
        actor = GameActor(service)
        try:
            with self.assertRaisesRegex(ValueError, "Projection unavailable"):
                await actor.observe("pilot:A")
            second = await asyncio.wait_for(actor.observe("pilot:A"), timeout=2)
            self.assertEqual({"observation": 2}, second)
            self.assertFalse(actor.requires_recovery)
        finally:
            if actor._worker.done():
                await asyncio.gather(actor._worker, return_exceptions=True)
            else:
                await asyncio.wait_for(actor.close(), timeout=2)

    async def test_close_cannot_overtake_a_request_waiting_for_mailbox_space(self):
        in_flight = asyncio.Event()
        release = asyncio.Event()

        class BlockingActor(GameActor):
            async def _handle_command(self, message):
                in_flight.set()
                await release.wait()
                return "finished"

        service = SimpleNamespace(session=SimpleNamespace(state=SimpleNamespace(game_id="actor-close")),
            observe=lambda *args, **kwargs: {"observed": True})
        actor = BlockingActor(service, queue_limit=1)
        envelope = CommandEnvelope(PROTOCOL_VERSION, "actor-close", "close-test", "decision", "pass", "cap", 0, {})
        first = asyncio.create_task(actor.command("pilot:A", envelope))
        await asyncio.wait_for(in_flight.wait(), timeout=2)
        queued = asyncio.create_task(actor.observe("pilot:A"))
        await asyncio.sleep(0)
        waiting = asyncio.create_task(actor.observe("pilot:B"))
        await asyncio.sleep(0)
        self.assertEqual(1, actor.queue_depth)
        self.assertFalse(waiting.done())
        release.set()
        closed = asyncio.create_task(actor.close())
        values = await asyncio.wait_for(asyncio.gather(first, queued, waiting, closed), timeout=2)
        self.assertEqual(["finished", {"observed": True}, {"observed": True}, None], values)
        self.assertEqual(0, actor.queue_depth)

    async def test_invalid_command_data_does_not_require_actor_recovery(self):
        session, service = self.make_service(32008)
        actor = GameActor(service)
        valid = self.envelope(session)
        try:
            for value in (chr(0xD800), float("nan")):
                receipt = await actor.command("pilot:A", replace(valid, choices={"unusable": value}))
                self.assertEqual("invalid_envelope", receipt.code)
                self.assertFalse(receipt.state_changed)
                self.assertFalse(actor.requires_recovery)
            self.assertEqual(0, len(session.commands))
            receipt = await actor.command("pilot:A", valid)
            self.assertTrue(receipt.ok)
            self.assertFalse(receipt.replayed)
            self.assertEqual(1, len(session.commands))
        finally:
            await actor.close()

    async def test_command_publishes_priority_after_its_request_waiter_disconnects(self):
        saving = threading.Event()
        release = threading.Event()
        published = asyncio.Event()

        class DelayedPersistence:
            def save(self, service):
                saving.set()
                if not release.wait(timeout=30):
                    raise TimeoutError("Test did not release persistence")

        session, service = self.make_service(32009)
        updates = []

        def notify(game_id):
            updates.append(game_id)
            published.set()

        actor = GameActor(service, persistence=DelayedPersistence(), publish_command_change=notify)
        envelope = self.envelope(session)
        request = asyncio.create_task(actor.command("pilot:A", envelope))
        try:
            self.assertTrue(await asyncio.to_thread(saving.wait, 30))
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertFalse(published.is_set())
            release.set()
            await asyncio.wait_for(published.wait(), timeout=30)
            self.assertEqual([session.state.game_id], updates)
            self.assertEqual(1, len(session.commands))
            self.assertTrue((await actor.command("pilot:A", envelope)).replayed)
            self.assertEqual([session.state.game_id], updates)
        finally:
            release.set()
            await actor.close()

    async def test_persistence_failure_sends_no_ack_and_fails_closed(self):
        # These bounds detect a deadlock; command preparation has no five-second SLA.
        watchdog_seconds = 30
        save_started = threading.Event()
        release_failure = threading.Event()

        class BrokenPersistence:
            def save(self, service):
                save_started.set()
                if not release_failure.wait(timeout=watchdog_seconds):
                    raise TimeoutError("test did not release persistence failure")
                raise OSError("disk unavailable")

        session, service = self.make_service(32004)
        manager = GameManager()
        actor = await manager.add(service, persistence=BrokenPersistence())
        command = asyncio.create_task(actor.command("pilot:A", self.envelope(session)))
        try:
            self.assertTrue(await asyncio.to_thread(save_started.wait, watchdog_seconds))
            self.assertFalse(command.done(), "A command must not be acknowledged before persistence completes")
            release_failure.set()
            with self.assertRaisesRegex(
                GameActorUnavailable, "failed durable command commit"
            ):
                await asyncio.wait_for(command, timeout=watchdog_seconds)
            self.assertEqual(1, len(session.commands))
            with self.assertRaisesRegex(
                GameActorUnavailable, "requires recovery"
            ):
                await asyncio.wait_for(actor.poll(), timeout=watchdog_seconds)
        finally:
            release_failure.set()
            if not command.done():
                command.cancel()
            await asyncio.gather(command, return_exceptions=True)
            await asyncio.wait_for(manager.close(), timeout=watchdog_seconds)

    async def test_slow_persistence_keeps_progress_observable_before_ack(self):
        # These are deadlock watchdogs, not a persistence performance budget.
        # First-command preparation may exceed five seconds on a loaded
        # cross-platform runner; the barriers still prove progress before ack.
        watchdog_seconds = 30
        save_started = threading.Event()
        release_save = threading.Event()

        class SlowPersistence:
            def save(self, service):
                save_started.set()
                if not release_save.wait(timeout=watchdog_seconds):
                    raise TimeoutError("test did not release persistence")
                return {
                    "authoritative_seconds": 0.25,
                    "derived_review_seconds": 0.0,
                    "total_seconds": 0.25,
                }

        session, service = self.make_service(32008)
        manager = GameManager()
        actor = await manager.add(service, persistence=SlowPersistence())
        command = asyncio.create_task(
            actor.command("pilot:A", self.envelope(session))
        )
        try:
            started = await asyncio.to_thread(save_started.wait, watchdog_seconds)
            self.assertTrue(started)

            # Persistence runs outside the event loop.  Public progress remains
            # readable even though the command cannot be acknowledged yet.
            await asyncio.sleep(0)
            snapshot = actor.progress_snapshot()
            self.assertEqual("command", snapshot["processing_kind"])
            self.assertTrue(snapshot["persistence"]["pending"])
            self.assertIsNotNone(
                snapshot["persistence"]["pending_seconds"]
            )
            self.assertFalse(command.done())

            release_save.set()
            receipt = await asyncio.wait_for(command, timeout=watchdog_seconds)
            self.assertTrue(receipt.ok)
            complete = actor.progress_snapshot()
            self.assertFalse(complete["persistence"]["pending"])
            self.assertGreaterEqual(
                complete["persistence"]["last_total_seconds"], 0.0
            )
            self.assertEqual(
                0.25,
                complete["persistence"][
                    "last_authoritative_seconds"
                ],
            )
            self.assertEqual(
                0.0,
                complete["persistence"]["last_derived_review_seconds"],
            )
        finally:
            release_save.set()
            await asyncio.wait_for(manager.close(), timeout=watchdog_seconds)

    async def test_network_projection_cursors_are_connection_isolated(self):
        session, service = self.make_service(32005)
        manager = GameManager()
        actor = await manager.add(service)
        first_key = "network:pilot:A:first"
        second_key = "network:pilot:A:second"
        try:
            first = await actor.observe(
                "pilot:A", full=True, cursor_key=first_key
            )
            second = await actor.observe(
                "pilot:A", full=True, cursor_key=second_key
            )
            self.assertEqual(first["view"], second["view"])
            self.assertEqual(1, first["pkt"])
            self.assertEqual(1, second["pkt"])
            receipt = await actor.command("pilot:A", self.envelope(session))
            self.assertTrue(receipt.ok)
            first_delta = await actor.observe(
                "pilot:A", cursor_key=first_key
            )
            second_delta = await actor.observe(
                "pilot:A", cursor_key=second_key
            )
            self.assertEqual("delta", first_delta["mode"])
            self.assertEqual("delta", second_delta["mode"])
            self.assertEqual(first["view"], first_delta["base"])
            self.assertEqual(second["view"], second_delta["base"])
            await actor.drop_projection_cursor(first_key)
            self.assertNotIn(first_key, session.cursors)
            self.assertIn(second_key, session.cursors)
        finally:
            await manager.close()

    async def test_browser_resume_cannot_override_a_rules_pause(self):
        session, service = self.make_service(32006)
        session.pause(
            {
                "kind": "semantic_unsupported",
                "label": "Material rules semantics require arbitration",
            }
        )
        manager = GameManager()
        actor = await manager.add(service)
        try:
            with self.assertRaisesRegex(
                GameLifecycleConflict,
                "Only an administrative stop",
            ):
                await asyncio.wait_for(actor.resume(), timeout=5)
            inspection = await asyncio.wait_for(actor.inspect(), timeout=5)
            self.assertEqual("paused", inspection["status"])
            self.assertEqual(
                "semantic_unsupported",
                inspection["pause_reason"]["kind"],
            )
        finally:
            await asyncio.wait_for(manager.close(), timeout=5)

    async def test_lifecycle_persistence_failure_fails_actor_closed(self):
        class BrokenPersistence:
            def save(self, service):
                raise OSError("record volume unavailable")

        _, service = self.make_service(32007)
        manager = GameManager()
        actor = await manager.add(service, persistence=BrokenPersistence())
        try:
            with self.assertRaisesRegex(
                GameActorUnavailable,
                "failed durable lifecycle commit",
            ):
                await asyncio.wait_for(
                    actor.pause("Persistence boundary"), timeout=5
                )
            with self.assertRaisesRegex(
                GameActorUnavailable,
                "requires recovery",
            ):
                await asyncio.wait_for(actor.inspect(), timeout=5)
        finally:
            await asyncio.wait_for(manager.close(), timeout=5)


if __name__ == "__main__":
    unittest.main()
