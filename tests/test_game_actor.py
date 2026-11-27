from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest

from common import load_assets, make_session
from quorune import (
    CommandEnvelope,
    GameActorClosed,
    GameActorUnavailable,
    GameManager,
    GameService,
    PROTOCOL_VERSION,
)
from quorune.runtime import GameActor, GameLifecycleConflict


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
