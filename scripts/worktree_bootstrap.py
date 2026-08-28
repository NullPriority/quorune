from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import shlex
import sqlite3
import subprocess
import sys
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.python_runtime import (
    SUPPORTED_PYTHON_TEXT,
    UnsupportedPythonRuntime,
    require_supported_runtime,
)
from scripts.install_dev_hooks import (
    HookInstallationError,
    check as check_hooks,
    install as install_hooks,
)
from scripts.test_shards import load_manifest, validate_partition
from scripts.validate_python_runtime import project_policy_failures


PINNED_CORPUS_REPORT = Path("coverage/oracle-coverage.json")
DEFAULT_DATABASE = Path("data/scryfall-current.sqlite3")
SNAPSHOT_FIELDS = (
    "schema_version",
    "card_count",
    "ruling_count",
    "oracle_source_sha256",
    "rulings_source_sha256",
    "scryfall_oracle_updated_at",
    "scryfall_rulings_updated_at",
)


class WorktreeBootstrapError(RuntimeError):
    """Raised when a worktree readiness authority is malformed."""


@dataclass(frozen=True)
class DatabaseInspection:
    status: str
    source: str
    path: str | None
    snapshot_authority: str = PINNED_CORPUS_REPORT.as_posix()
    mismatches: tuple[str, ...] = ()
    error: str | None = None


def _git(
    root: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _worktree_top_level(root: Path) -> Path:
    result = _git(root, "rev-parse", "--show-toplevel")
    if result.returncode:
        raise WorktreeBootstrapError(
            result.stderr.strip() or "not a Git worktree"
        )
    return Path(result.stdout.strip()).resolve()


def _tracked_hook(root: Path) -> None:
    result = _git(
        root,
        "ls-files",
        "--error-unmatch",
        ".githooks/pre-push",
    )
    if result.returncode:
        raise WorktreeBootstrapError(
            ".githooks/pre-push is not tracked in this worktree"
        )


def _local_python(root: Path) -> tuple[Path | None, str]:
    windows = (
        root / ".venv" / "Scripts" / "python.exe",
        r".\.venv\Scripts\python.exe",
    )
    posix = (root / ".venv" / "bin" / "python", "./.venv/bin/python")
    candidates = (windows, posix) if os.name == "nt" else (posix, windows)
    for path, display in candidates:
        if path.is_file():
            return path, display
    return None, (
        r".\.venv\Scripts\python.exe"
        if os.name == "nt"
        else "./.venv/bin/python"
    )


def _is_linked_directory(path: Path) -> bool:
    """Return whether a directory aliases storage outside this worktree."""

    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def inspect_runtime(
    root: Path,
    *,
    executable: Path | None = None,
    implementation: str | None = None,
    version_info: object | None = None,
    maxsize: int | None = None,
    prefix: Path | None = None,
    base_prefix: Path | None = None,
) -> dict[str, object]:
    launcher, display = _local_python(root)
    running = (executable or Path(sys.executable)).resolve()
    failures = list(project_policy_failures(root))
    if _is_linked_directory(root / ".venv"):
        failures.append(
            "worktree-local .venv must be a real directory, not a symlink or junction"
        )
    try:
        require_supported_runtime(
            version_info or sys.version_info,
            implementation=implementation or platform.python_implementation(),
            maxsize=sys.maxsize if maxsize is None else maxsize,
        )
    except UnsupportedPythonRuntime as exc:
        failures.append(str(exc))
    active_prefix = (prefix or Path(sys.prefix)).resolve()
    system_prefix = (base_prefix or Path(sys.base_prefix)).resolve()
    if active_prefix == system_prefix:
        failures.append("bootstrap must run from a virtual environment")
    if launcher is None:
        failures.append(
            "worktree-local .venv Python launcher is missing"
        )
    else:
        if not os.access(launcher, os.X_OK):
            failures.append(
                f"worktree-local Python launcher is not executable: {display}"
            )
        try:
            if not os.path.samefile(launcher, running):
                failures.append(
                    "bootstrap must run through the worktree-local .venv "
                    f"launcher ({display})"
                )
        except OSError as exc:
            failures.append(f"unable to compare Python launchers: {exc}")
    return {
        "ok": not failures,
        "required": f"CPython {SUPPORTED_PYTHON_TEXT} x64",
        "executable": str(running),
        "environment_prefix": str(active_prefix),
        "launcher": display,
        "failures": failures,
    }


def _resolve_database(
    root: Path,
    argument: str | None,
    environment: Mapping[str, str],
) -> tuple[Path | None, str]:
    raw: str | None
    if argument:
        raw, source = argument, "--db"
    elif environment.get("MTG_CARD_DB"):
        raw, source = environment["MTG_CARD_DB"], "MTG_CARD_DB"
    else:
        raw, source = str(DEFAULT_DATABASE), "worktree-default"
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path.resolve(), source


def _pinned_snapshot(root: Path) -> dict[str, str]:
    path = root / PINNED_CORPUS_REPORT
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorktreeBootstrapError(
            f"unable to load pinned corpus report: {path}"
        ) from exc
    snapshot = report.get("card_data_snapshot")
    if not isinstance(snapshot, dict):
        raise WorktreeBootstrapError(
            f"pinned corpus report has no card_data_snapshot: {path}"
        )
    missing = [field for field in SNAPSHOT_FIELDS if not snapshot.get(field)]
    if missing:
        raise WorktreeBootstrapError(
            "pinned corpus snapshot is missing: " + ", ".join(missing)
        )
    return {field: str(snapshot[field]) for field in SNAPSHOT_FIELDS}


def _read_database_metadata(path: Path) -> dict[str, str]:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            "SELECT key, value FROM metadata ORDER BY key"
        ).fetchall()
    finally:
        connection.close()
    return {str(key): str(value) for key, value in rows}


def inspect_database(
    root: Path,
    *,
    argument: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> DatabaseInspection:
    path, source = _resolve_database(
        root,
        argument,
        os.environ if environment is None else environment,
    )
    if path is None or not path.is_file():
        return DatabaseInspection(
            status="missing",
            source=source,
            path=str(path) if path is not None else None,
            error="pinned card database does not exist",
        )
    try:
        expected = _pinned_snapshot(root)
        observed = _read_database_metadata(path)
    except (OSError, sqlite3.DatabaseError, WorktreeBootstrapError) as exc:
        return DatabaseInspection(
            status="invalid",
            source=source,
            path=str(path),
            error=str(exc),
        )
    mismatches = tuple(
        field
        for field in SNAPSHOT_FIELDS
        if observed.get(field) != expected[field]
    )
    return DatabaseInspection(
        status="stale" if mismatches else "current",
        source=source,
        path=str(path),
        mismatches=mismatches,
        error=(
            "database metadata does not match the tracked compiler corpus snapshot"
            if mismatches
            else None
        ),
    )


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def finalizer_commands(
    root: Path,
    database: DatabaseInspection,
) -> dict[str, object]:
    _launcher, python = _local_python(root)
    base = [python, "scripts/finalize_generated.py", "--write"]
    database_command: list[str] | None = None
    if database.path is not None and Path(database.path).is_file():
        database_command = [
            *base,
            "--db",
            _display_path(root, Path(database.path)),
        ]
    recommended = (
        database_command
        if database.status == "current"
        else base
    )
    display = (
        subprocess.list2cmdline(recommended)
        if os.name == "nt"
        else shlex.join(recommended)
    )
    return {
        "standard": base,
        "database_backed": database_command,
        "recommended": recommended,
        "recommended_display": display,
    }


def inspect_worktree(
    root: Path = ROOT,
    *,
    database_argument: str | None = None,
    environment: Mapping[str, str] | None = None,
    install_hook: bool = False,
    runtime_options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    root = root.resolve()
    failures: list[str] = []
    try:
        top_level = _worktree_top_level(root)
        if top_level != root:
            failures.append(
                f"requested root is not the worktree top level: {top_level}"
            )
        _tracked_hook(root)
    except WorktreeBootstrapError as exc:
        failures.append(str(exc))

    runtime = inspect_runtime(root, **dict(runtime_options or {}))
    failures.extend(str(value) for value in runtime["failures"])

    hook_status = "ready"
    try:
        if install_hook:
            install_hooks(root)
        else:
            check_hooks(root)
    except HookInstallationError as exc:
        hook_status = "error"
        failures.append(str(exc))

    try:
        shard_summary = validate_partition(
            load_manifest(root / "platform" / "test-shards.json"),
            tests_root=root / "tests",
        )
        shards: dict[str, object] = {"ok": True, **shard_summary}
    except (OSError, ValueError) as exc:
        shards = {"ok": False, "error": str(exc)}
        failures.append(f"test-shard ownership is invalid: {exc}")

    database = inspect_database(
        root,
        argument=database_argument,
        environment=environment,
    )
    if database.status != "current":
        failures.append(
            f"card database is {database.status}: "
            f"{database.error or database.path}"
        )

    return {
        "ok": not failures,
        "root": str(root),
        "runtime": runtime,
        "hook": {
            "ok": hook_status == "ready",
            "status": hook_status,
            "configuration_scope": "repository-local",
            "install_requested": install_hook,
        },
        "database": asdict(database),
        "test_shards": shards,
        "finalization": finalizer_commands(root, database),
        "failures": failures,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a Quorune worktree's Python, hook, pinned database, "
            "test shards, and generated-finalization readiness"
        )
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--db")
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help=(
            "Install the tracked hook through repository-local Git config; "
            "foreign hook policies are preserved"
        ),
    )
    args = parser.parse_args(argv)
    report = inspect_worktree(
        args.root,
        database_argument=args.db,
        install_hook=args.install_hook,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
