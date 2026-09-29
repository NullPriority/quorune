from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import load_assets, make_session
from quorune import (
    CommandEnvelope,
    DirectoryGamePersistence,
    GameManager,
    GameService,
    PROTOCOL_VERSION,
    SqliteIdempotencyRepository,
)
from quorune.review_artifacts import write_review_artifacts


class ServerPersistenceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int):
        return make_session(
            self.db, self.mishra, self.zimone, seed=seed
        )

    @staticmethod
    def envelope(session, *, command_id: str = "persist-1"):
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

    async def test_actor_persists_before_ack_and_restores_pending_game(self):
        session = self.make_session(33001)
        envelope = self.envelope(session)
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(Path(tmp) / "games")
            manager = GameManager()
            actor = await manager.add(
                GameService(session), persistence=persistence
            )
            receipt = await actor.command("pilot:A", envelope)
            self.assertTrue(receipt.ok)
            self.assertTrue(persistence.exists(session.state.game_id))
            record_dir = persistence.game_directory(session.state.game_id)
            self.assertEqual(
                1,
                len(
                    (record_dir / "commands.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ),
            )
            self.assertFalse((record_dir / "review.json").exists())
            self.assertFalse((record_dir / "review.md").exists())
            await manager.close()

            restored = persistence.load(self.db, session.state.game_id)
            self.assertEqual(1, len(restored.session.commands))
            self.assertEqual(
                "persist-1",
                restored.session.commands[0]["client_command_id"],
            )
            self.assertEqual(
                ["pilot:B"], restored.session.pending_principals()
            )
            replayed = restored.command(envelope, principal="pilot:A")
            self.assertTrue(replayed.ok)
            self.assertTrue(replayed.replayed)
            self.assertEqual(1, len(restored.session.commands))

    async def test_live_save_defers_review_until_terminal_state(self):
        session = self.make_session(33004)
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(Path(tmp) / "games")
            service = GameService(session)
            live_timing = persistence.save(service)
            record_dir = persistence.game_directory(session.state.game_id)

            self.assertTrue((record_dir / "manifest.json").exists())
            self.assertFalse((record_dir / "review.json").exists())
            self.assertFalse((record_dir / "review.md").exists())
            self.assertGreaterEqual(
                live_timing["authoritative_seconds"], 0.0
            )
            self.assertGreaterEqual(
                live_timing["derived_review_seconds"], 0.0
            )

            session.pause(
                {
                    "kind": "administrative_stop",
                    "label": "Focused persistence test",
                }
            )
            paused_timing = persistence.save(service)
            self.assertTrue((record_dir / "review.json").exists())
            self.assertTrue((record_dir / "review.md").exists())
            self.assertGreaterEqual(
                paused_timing["derived_review_seconds"], 0.0
            )

            session.resume()
            persistence.save(service)
            self.assertFalse((record_dir / "review.json").exists())
            self.assertFalse((record_dir / "review.md").exists())

            session.state.game_over = True
            session.state.winner = "A"
            session.record_status = "complete"
            persistence.save(service)

            self.assertTrue((record_dir / "review.json").exists())
            self.assertTrue((record_dir / "review.md").exists())

    async def test_interrupted_review_replace_preserves_prior_review(self):
        session = self.make_session(33005)
        session.pause(
            {
                "kind": "administrative_stop",
                "label": "Atomic review interruption test",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(Path(tmp) / "games")
            service = GameService(session)
            persistence.save(service)
            record_dir = persistence.game_directory(session.state.game_id)
            review_path = record_dir / "review.json"
            previous = review_path.read_bytes()
            json.loads(previous)

            from quorune import review_artifacts as report_module

            real_replace = report_module.os.replace

            def fail_review_replace(source, target):
                if Path(target).name == "review.json":
                    raise OSError("injected review replace interruption")
                return real_replace(source, target)

            with mock.patch.object(
                report_module.os, "replace", side_effect=fail_review_replace
            ):
                with self.assertRaisesRegex(
                    OSError, "injected review replace interruption"
                ):
                    write_review_artifacts(
                        record_dir,
                        session.engine,
                        decisions=session.decisions,
                        manifest=json.loads(
                            (record_dir / "manifest.json").read_text(
                                encoding="utf-8"
                            )
                        ),
                    )

            self.assertEqual(previous, review_path.read_bytes())
            json.loads(review_path.read_text(encoding="utf-8"))
            restored = persistence.load(self.db, session.state.game_id)
            self.assertEqual(session.state.revision, restored.session.state.revision)

    async def test_concurrent_review_writers_leave_complete_artifacts(self):
        session = self.make_session(33006)
        session.pause(
            {
                "kind": "administrative_stop",
                "label": "Concurrent review writer test",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(Path(tmp) / "games")
            service = GameService(session)
            persistence.save(service)
            record_dir = persistence.game_directory(session.state.game_id)
            manifest = json.loads(
                (record_dir / "manifest.json").read_text(encoding="utf-8")
            )

            def write_review():
                return write_review_artifacts(
                    record_dir,
                    session.engine,
                    decisions=session.decisions,
                    manifest=manifest,
                )

            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda _: write_review(), range(8)))

            self.assertEqual(8, len(results))
            json.loads(
                (record_dir / "manifest.json").read_text(encoding="utf-8")
            )
            json.loads(
                (record_dir / "review.json").read_text(encoding="utf-8")
            )
            self.assertTrue(
                (record_dir / "review.md").read_text(encoding="utf-8")
            )
            self.assertEqual([], list(record_dir.glob(".*.tmp")))
            restored = persistence.load(self.db, session.state.game_id)
            self.assertEqual(session.state.revision, restored.session.state.revision)

    async def test_review_size_fixed_point_derives_semantics_once(self):
        session = self.make_session(33007)
        session.pause(
            {
                "kind": "administrative_stop",
                "label": "Single semantic derivation test",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(Path(tmp) / "games")
            service = GameService(session)
            record_dir = persistence.game_directory(session.state.game_id)

            from quorune import report as report_module

            with mock.patch.object(
                report_module,
                "derive_review",
                wraps=report_module.derive_review,
            ) as derive:
                persistence.save(service)

            self.assertEqual(1, derive.call_count)
            review = json.loads(
                (record_dir / "review.json").read_text(encoding="utf-8")
            )
            sizes = review["size_comparison"]
            self.assertEqual(
                (record_dir / "review.json").stat().st_size
                + (record_dir / "review.md").stat().st_size,
                sizes["review_artifact_bytes"],
            )
            self.assertEqual(
                sum(
                    path.stat().st_size
                    for path in record_dir.iterdir()
                    if path.is_file()
                ),
                sizes["complete_record_bytes"],
            )

    async def test_sqlite_idempotency_survives_service_restart_without_token(self):
        session = self.make_session(33002)
        envelope = self.envelope(session, command_id="durable-1")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "server.sqlite3"
            repository = SqliteIdempotencyRepository(database)
            service = GameService(session, idempotency=repository)
            receipt = service.command(envelope, principal="pilot:A")
            self.assertTrue(receipt.ok)
            self.assertNotIn(envelope.capability.encode("utf-8"), database.read_bytes())

            persistence = DirectoryGamePersistence(root / "games")
            persistence.save(service)
            restarted = persistence.load(
                self.db,
                session.state.game_id,
                idempotency=SqliteIdempotencyRepository(database),
            )
            duplicate = restarted.command(envelope, principal="pilot:A")
            self.assertTrue(duplicate.ok)
            self.assertTrue(duplicate.replayed)

    async def test_persisted_checkpoint_contains_no_raw_capability(self):
        session = self.make_session(33003)
        envelope = self.envelope(session)
        token = envelope.capability
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(tmp)
            service = GameService(session)
            self.assertTrue(
                service.command(envelope, principal="pilot:A").ok
            )
            persistence.save(service)
            record_dir = persistence.game_directory(session.state.game_id)
            for name in (
                "checkpoint.json",
                "commands.jsonl",
                "manifest.json",
            ):
                self.assertNotIn(
                    token,
                    (record_dir / name).read_text(encoding="utf-8"),
                    name,
                )

    async def test_game_directory_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(tmp)
            with self.assertRaisesRegex(ValueError, "Invalid game id"):
                persistence.game_directory("../checkpoint")
            with self.assertRaisesRegex(ValueError, "Invalid game id"):
                persistence.game_directory("C:/outside")


if __name__ == "__main__":
    unittest.main()
