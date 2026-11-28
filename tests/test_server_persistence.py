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
            service = GameService(session)
            persistence.save(service)
            record_dir = persistence.game_directory(session.state.game_id)
            semantics_path = record_dir / "semantics.json"
            semantics_bytes = semantics_path.read_bytes()
            write_bytes = Path.write_bytes

            def require_snapshot_reuse(path, data):
                self.assertNotEqual(semantics_path.with_suffix(".json.tmp"), path)
                return write_bytes(path, data)

            manager = GameManager()
            actor = await manager.add(service, persistence=persistence)
            with mock.patch.object(Path, "write_bytes", require_snapshot_reuse), mock.patch(
                "quorune.session.record_semantics_provenance",
                side_effect=AssertionError("An unchanged saved snapshot already attested its provenance"),
            ):
                receipt = await actor.command("pilot:A", envelope)
            self.assertTrue(receipt.ok)
            self.assertTrue(persistence.exists(session.state.game_id))
            self.assertEqual(semantics_bytes, semantics_path.read_bytes())
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

    def test_verified_recovery_reuses_runtime_trust_on_the_first_save(self):
        from quorune.record import replay_record
        from quorune.record_trust import runtime_trust_provenance

        session = self.make_session(33016)
        with tempfile.TemporaryDirectory() as tmp:
            original = DirectoryGamePersistence(tmp)
            original.save(GameService(session))
            game_id = session.state.game_id
            directory = original.game_directory(game_id)
            manifest_path = directory / "manifest.json"
            runtime = json.loads(manifest_path.read_bytes())["runtime_trust"]

            fresh = DirectoryGamePersistence(tmp)
            with mock.patch(
                "quorune.persistence.runtime_trust_provenance",
                side_effect=AssertionError("Recovery already validated this runtime trust"),
            ):
                restored = fresh.load(self.db, game_id, verify_rules=True)
                command = self.envelope(restored.session, command_id="verified-recovery-keep")
                receipt = restored.command(command, principal="pilot:A")
                self.assertTrue(receipt.ok, receipt.summary)
                fresh.save(restored)
            self.assertEqual(runtime, json.loads(manifest_path.read_bytes())["runtime_trust"])
            replay = replay_record(directory, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])

            for verify_rules in (False, True):
                with self.subTest(legacy_manifest=verify_rules):
                    if verify_rules:
                        manifest = json.loads(manifest_path.read_bytes())
                        manifest.pop("runtime_trust")
                        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    fresh = DirectoryGamePersistence(tmp)
                    with mock.patch("quorune.persistence.runtime_trust_provenance",
                                    wraps=runtime_trust_provenance) as derive:
                        restored = fresh.load(self.db, game_id, verify_rules=verify_rules)
                        derive.assert_not_called()
                        fresh.save(restored)
                        derive.assert_called_once()
                    self.assertEqual(runtime, json.loads(manifest_path.read_bytes())["runtime_trust"])

    async def test_semantic_snapshot_rechecks_nested_content_and_restored_file(self):
        from quorune import record

        session = self.make_session(33009)
        card = session.engine.semantics.card_programs()[0]
        card.provenance["save_probe"] = {"note": "original", "source": "test"}
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(tmp)
            service = GameService(session)
            persistence.save(service)
            directory = persistence.game_directory(session.state.game_id)
            snapshot = directory / "semantics.json"
            original = snapshot.read_bytes()
            original_manifest = (directory / "manifest.json").read_bytes()

            card.provenance["save_probe"]["note"] = "updated in place"
            write_journal = record._atomic_jsonl

            def fail_after_snapshot(path, rows):
                if path.name == "commands.jsonl":
                    self.assertNotEqual(original, snapshot.read_bytes())
                    raise OSError("Interrupted after writing the new semantic snapshot")
                return write_journal(path, rows)

            with mock.patch.object(record, "_atomic_jsonl", fail_after_snapshot):
                with self.assertRaisesRegex(OSError, "Interrupted after writing"):
                    persistence.save(service)
            self.assertEqual(original, snapshot.read_bytes())
            self.assertEqual(original_manifest, (directory / "manifest.json").read_bytes())

            persistence.save(service)
            committed = snapshot.read_bytes()
            raw_program = json.loads(committed)["card_programs"][card.oracle_id]
            self.assertEqual("updated in place", raw_program["provenance"]["save_probe"]["note"])
            manifest = json.loads((directory / "manifest.json").read_bytes())
            self.assertEqual(raw_program["fingerprint"], manifest["card_programs"]["fingerprints"][card.oracle_id])

            snapshot.unlink()
            persistence.save(service)
            self.assertEqual(committed, snapshot.read_bytes())

            card.provenance["save_probe"] = {"source": "test", "note": "updated in place"}
            persistence.save(service)
            saved = json.loads(snapshot.read_bytes())["card_programs"][card.oracle_id]["provenance"]
            self.assertEqual(["source", "note"], list(saved["save_probe"]))

    async def test_live_save_defers_review_until_terminal_state(self):
        session = self.make_session(33004)
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(Path(tmp) / "games")
            service = GameService(session)
            live_timing = persistence.save(service)
            record_dir = persistence.game_directory(session.state.game_id)
            audit = record_dir / "hidden-information-audit.json"

            self.assertTrue((record_dir / "manifest.json").exists())
            self.assertFalse((record_dir / "review.json").exists())
            self.assertFalse((record_dir / "review.md").exists())
            self.assertFalse(audit.exists())
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
            self.assertTrue(audit.exists())
            self.assertGreaterEqual(
                paused_timing["derived_review_seconds"], 0.0
            )

            session.resume()
            persistence.save(service)
            self.assertFalse((record_dir / "review.json").exists())
            self.assertFalse((record_dir / "review.md").exists())
            self.assertFalse(audit.exists())

            session.state.game_over = True
            session.state.winner = "A"
            session.record_status = "complete"
            persistence.save(service)

            self.assertTrue((record_dir / "review.json").exists())
            self.assertTrue((record_dir / "review.md").exists())
            self.assertTrue(audit.exists())

    async def test_new_persistence_restores_a_pending_save_after_interruption(self):
        from quorune import persistence as storage

        session = self.make_session(33008)
        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(Path(tmp) / "games")
            service = GameService(session)
            persistence.save(service)
            game_id = session.state.game_id
            directory = persistence.game_directory(game_id)
            before = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
            session.pause({"kind": "administrative_stop", "label": "Interrupted save"})
            write_text = Path.write_text
            restore_interrupted_record = storage._restore_interrupted_record

            def tear_during_pilot_metadata(path, data, *args, **kwargs):
                if path == directory / "cursors.json":
                    self.assertNotEqual(before["manifest.json"], (directory / "manifest.json").read_bytes())
                    write_text(path, "{", encoding="utf-8")
                    raise OSError("Interrupted pilot metadata write")
                return write_text(path, data, *args, **kwargs)

            def interrupt_before_rollback(path):
                if (path / storage._SAVE_RECOVERY_DIRECTORY).is_dir():
                    raise OSError("Process stopped before rollback could finish")
                return restore_interrupted_record(path)

            with mock.patch.object(Path, "write_text", tear_during_pilot_metadata), mock.patch.object(
                storage, "_restore_interrupted_record", side_effect=interrupt_before_rollback,
            ), self.assertRaisesRegex(OSError, "Interrupted pilot metadata write") as error:
                persistence.save(service)
            self.assertIn("Record rollback deferred: OSError", error.exception.__notes__)
            self.assertTrue((directory / storage._SAVE_RECOVERY_DIRECTORY).is_dir())

            recovered = DirectoryGamePersistence(persistence.root).load(self.db, game_id)
            self.assertIn(recovered.session.record_status, {"created", "in_progress"})
            self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()})
            self.assertFalse((directory / storage._SAVE_RECOVERY_DIRECTORY).exists())

    async def test_incomplete_recovery_backup_leaves_current_record_alone(self):
        from quorune import persistence as storage

        with tempfile.TemporaryDirectory() as tmp:
            persistence = DirectoryGamePersistence(tmp)
            game_id = "incomplete-record-backup"
            directory = persistence.game_directory(game_id)
            (directory / storage._SAVE_RECOVERY_DIRECTORY).mkdir(parents=True)
            checkpoint = directory / "checkpoint.json"
            checkpoint.write_text("original checkpoint", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "backup is incomplete"):
                persistence.recover_interrupted_save(game_id)
            self.assertEqual("original checkpoint", checkpoint.read_text(encoding="utf-8"))

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
