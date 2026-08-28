from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.worktree_bootstrap import (
    DatabaseInspection,
    SNAPSHOT_FIELDS,
    finalizer_commands,
    inspect_database,
    inspect_runtime,
    inspect_worktree,
)


SNAPSHOT = {
    "schema_version": "2",
    "card_count": "100",
    "ruling_count": "200",
    "oracle_source_sha256": "a" * 64,
    "rulings_source_sha256": "b" * 64,
    "scryfall_oracle_updated_at": "2026-08-01T00:00:00+00:00",
    "scryfall_rulings_updated_at": "2026-08-01T01:00:00+00:00",
}


def _write_repository_policy(root: Path) -> None:
    (root / ".python-version").write_text("3.12\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "\n".join(
            (
                "[project]",
                "name = 'fixture'",
                "version = '1.0'",
                "requires-python = '>=3.12,<3.13'",
                "classifiers = [",
                "  'Programming Language :: Python :: 3.12',",
                "  'Programming Language :: Python :: 3 :: Only',",
                "  'Programming Language :: Python :: Implementation :: CPython',",
                "]",
                "",
            )
        ),
        encoding="utf-8",
    )


def _write_launcher(root: Path) -> Path:
    relative = (
        Path(".venv/Scripts/python.exe")
        if os.name == "nt"
        else Path(".venv/bin/python")
    )
    launcher = root / relative
    launcher.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, launcher)
    return launcher


def _write_snapshot(root: Path, snapshot: dict[str, str] = SNAPSHOT) -> None:
    coverage = root / "coverage"
    coverage.mkdir(parents=True, exist_ok=True)
    (coverage / "oracle-coverage.json").write_text(
        json.dumps({"card_data_snapshot": snapshot}),
        encoding="utf-8",
    )


def _write_database(path: Path, metadata: dict[str, str] = SNAPSHOT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE metadata(key TEXT, value TEXT)")
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        connection.commit()
    finally:
        connection.close()


def _write_shards(root: Path) -> None:
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_fixture.py").write_text("# fixture\n", encoding="utf-8")
    platform = root / "platform"
    platform.mkdir()
    (platform / "test-shards.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "execution_order": ["only"],
                "primary_shards": {"only": ["test_fixture"]},
                "overlay_suites": {"smoke": ["test_fixture"]},
            }
        ),
        encoding="utf-8",
    )


def _runtime_options(root: Path, launcher: Path) -> dict[str, object]:
    return {
        "executable": launcher,
        "prefix": root / ".venv",
        "base_prefix": Path(sys.base_prefix),
    }


class WorktreeBootstrapTests(unittest.TestCase):
    def test_database_status_distinguishes_current_stale_missing_and_invalid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_snapshot(root)
            current = root / "current.sqlite3"
            _write_database(current)

            observed = inspect_database(root, argument=str(current), environment={})
            self.assertEqual("current", observed.status)
            self.assertEqual((), observed.mismatches)

            stale = root / "stale.sqlite3"
            stale_metadata = dict(SNAPSHOT)
            stale_metadata["oracle_source_sha256"] = "c" * 64
            _write_database(stale, stale_metadata)
            observed = inspect_database(root, argument=str(stale), environment={})
            self.assertEqual("stale", observed.status)
            self.assertEqual(("oracle_source_sha256",), observed.mismatches)

            observed = inspect_database(
                root,
                argument=str(root / "missing.sqlite3"),
                environment={},
            )
            self.assertEqual("missing", observed.status)
            commands = finalizer_commands(root, observed)
            self.assertIsNone(commands["database_backed"])
            self.assertEqual(commands["standard"], commands["recommended"])

            invalid = root / "invalid.sqlite3"
            invalid.write_text("not sqlite", encoding="utf-8")
            observed = inspect_database(root, argument=str(invalid), environment={})
            self.assertEqual("invalid", observed.status)

    def test_explicit_database_precedes_environment_and_worktree_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_snapshot(root)
            explicit = root / "explicit.sqlite3"
            environment = root / "environment.sqlite3"
            default = root / "data" / "scryfall-current.sqlite3"
            for path in (explicit, environment, default):
                _write_database(path)

            observed = inspect_database(
                root,
                argument=str(explicit),
                environment={"MTG_CARD_DB": str(environment)},
            )
            self.assertEqual("--db", observed.source)
            self.assertEqual(explicit.resolve(), Path(observed.path))

            observed = inspect_database(
                root,
                environment={"MTG_CARD_DB": str(environment)},
            )
            self.assertEqual("MTG_CARD_DB", observed.source)
            self.assertEqual(environment.resolve(), Path(observed.path))

            observed = inspect_database(root, environment={})
            self.assertEqual("worktree-default", observed.source)
            self.assertEqual(default.resolve(), Path(observed.path))

    def test_runtime_requires_the_worktree_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_repository_policy(root)
            launcher = _write_launcher(root)

            accepted = inspect_runtime(root, **_runtime_options(root, launcher))
            self.assertTrue(accepted["ok"])

            global_runtime = inspect_runtime(
                root,
                executable=launcher,
                prefix=Path(sys.base_prefix),
                base_prefix=Path(sys.base_prefix),
            )
            self.assertFalse(global_runtime["ok"])
            self.assertTrue(
                any(
                    "virtual environment" in item
                    for item in global_runtime["failures"]
                )
            )

            other = root / "other-python"
            other.write_bytes(b"")
            rejected = inspect_runtime(
                root,
                executable=other,
                prefix=root / ".venv",
                base_prefix=Path(sys.base_prefix),
            )
            self.assertFalse(rejected["ok"])
            self.assertTrue(
                any("worktree-local" in item for item in rejected["failures"])
            )

    def test_runtime_rejects_a_linked_worktree_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_repository_policy(root)
            launcher = _write_launcher(root)

            with mock.patch(
                "scripts.worktree_bootstrap._is_linked_directory",
                return_value=True,
            ):
                report = inspect_runtime(
                    root, **_runtime_options(root, launcher)
                )

            self.assertFalse(report["ok"])
            self.assertTrue(
                any(
                    "symlink or junction" in failure
                    for failure in report["failures"]
                )
            )

    def test_database_backed_finalizer_uses_the_detected_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_launcher(root)
            database = root / "data" / "scryfall-current.sqlite3"
            database.parent.mkdir(parents=True)
            database.touch()
            inspection = DatabaseInspection(
                status="current",
                source="worktree-default",
                path=str(database),
            )

            commands = finalizer_commands(root, inspection)
            self.assertEqual(
                [
                    commands["standard"][0],
                    "scripts/finalize_generated.py",
                    "--write",
                    "--db",
                    "data/scryfall-current.sqlite3",
                ],
                commands["recommended"],
            )

    def test_install_mode_changes_only_repository_local_hook_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            _write_repository_policy(root)
            launcher = _write_launcher(root)
            _write_snapshot(root)
            _write_database(root / "data" / "scryfall-current.sqlite3")
            _write_shards(root)
            hook = root / ".githooks" / "pre-push"
            hook.parent.mkdir()
            hook.write_text("#!/bin/sh\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", ".githooks/pre-push"],
                cwd=root,
                check=True,
            )

            report = inspect_worktree(
                root,
                install_hook=True,
                environment={},
                runtime_options=_runtime_options(root, launcher),
            )

            self.assertTrue(report["ok"], report["failures"])
            self.assertEqual("repository-local", report["hook"]["configuration_scope"])
            configured = subprocess.check_output(
                ["git", "config", "--local", "--get", "core.hooksPath"],
                cwd=root,
                text=True,
                encoding="utf-8",
            ).strip()
            self.assertEqual(".githooks", configured)
            self.assertEqual(
                set(SNAPSHOT_FIELDS),
                set(SNAPSHOT),
            )

    def test_foreign_repository_hook_policy_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            _write_repository_policy(root)
            launcher = _write_launcher(root)
            _write_snapshot(root)
            _write_database(root / "data" / "scryfall-current.sqlite3")
            _write_shards(root)
            hook = root / ".githooks" / "pre-push"
            hook.parent.mkdir()
            hook.write_text("#!/bin/sh\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", ".githooks/pre-push"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "--local", "core.hooksPath", "custom-hooks"],
                cwd=root,
                check=True,
            )

            report = inspect_worktree(
                root,
                install_hook=True,
                environment={},
                runtime_options=_runtime_options(root, launcher),
            )

            self.assertFalse(report["ok"])
            configured = subprocess.check_output(
                ["git", "config", "--local", "--get", "core.hooksPath"],
                cwd=root,
                text=True,
                encoding="utf-8",
            ).strip()
            self.assertEqual("custom-hooks", configured)


if __name__ == "__main__":
    unittest.main()
