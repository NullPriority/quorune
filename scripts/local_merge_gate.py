from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from scripts.demo_four_player_protocol import validate_protocol_output
from scripts.validate_python_runtime import require_supported_python
DEFAULT_FOCUSED_TESTS = (
    "tests.test_seed_20260730_regression",
    "tests.test_decision_opportunities",
    "tests.test_game_record_v3",
    "tests.test_command_zone_rules",
)
PRIVACY_TESTS = (
    "tests.test_permissions_projection",
    "tests.test_semantic_searches",
    "tests.test_native_v3_slice",
    "tests.test_public_fixtures",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


@dataclass(frozen=True)
class GateStep:
    name: str
    command: tuple[str, ...]


@dataclass
class StepResult:
    name: str
    command: list[str]
    started_at: str
    completed_at: str
    duration_seconds: float
    returncode: int
    log: str


def build_steps(
    python: str,
    *,
    database: Path,
    output: Path,
    focused_tests: Sequence[str],
    npm: str = "npm",
    card_databases: Sequence[Path] = (ROOT / "data/scryfall-current.sqlite3",),
) -> list[GateStep]:
    protocol_output = output / "protocol-demo"
    wheel_output = output / "dist"
    card_database_arguments = tuple(arg for path in card_databases for arg in ("--db", str(path)))
    return [
        GateStep(
            "python_runtime_policy",
            (python, "scripts/validate_python_runtime.py"),
        ),
        GateStep(
            "card_program_source_fingerprints",
            (
                python, "scripts/audit_card_program_fingerprints.py", *card_database_arguments,
                "--output", str(output / "card-program-fingerprints.json"),
            ),
        ),
        GateStep(
            "generated_capability_evidence_freshness",
            (
                python,
                "scripts/update_capability_evidence.py",
                "--check",
            ),
        ),
        GateStep(
            "generated_card_unlock_frontier_freshness",
            (
                python,
                "scripts/update_card_unlock_frontier.py",
                "--check",
            ),
        ),
        GateStep(
            "generated_reusable_piece_freshness",
            (
                python,
                "scripts/update_reusable_piece_matrix.py",
                "--check",
            ),
        ),
        GateStep(
            "generated_ci_escape_report_freshness",
            (
                python,
                "scripts/update_ci_escape_report.py",
                "--check",
            ),
        ),
        GateStep(
            "generated_rules_scheduler_freshness",
            (
                python,
                "scripts/update_rules_scheduler.py",
                "--check",
            ),
        ),
        GateStep(
            "module_classification_freshness",
            (
                python,
                "scripts/update_module_classifications.py",
                "--check",
            ),
        ),
        GateStep(
            "continuous_effect_work_budget",
            (
                python,
                "scripts/benchmark_continuous_effects.py",
                "--check",
            ),
        ),
        GateStep(
            "generated_platform_freshness",
            (
                python,
                "scripts/update_platform_status.py",
                "--check",
            ),
        ),
        GateStep(
            "compile",
            (
                python,
                "-m",
                "compileall",
                "-q",
                "quorune",
                "tests",
                "scripts",
                "simctl.py",
            ),
        ),
        GateStep(
            "compact_ci_dependency_closure",
            (
                python,
                "scripts/build_test_database.py",
                "validate-ci-dependencies",
            ),
        ),
        GateStep(
            "generated_compact_ci_dependency_freshness",
            (
                python,
                "scripts/update_compact_ci_dependencies.py",
                "--check",
            ),
        ),
        GateStep(
            "build_test_database",
            (
                python,
                "scripts/build_test_database.py",
                "build-ci",
                "--output",
                str(database),
            ),
        ),
        GateStep(
            "full_deterministic_suite",
            (
                python,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
                "-v",
            ),
        ),
        GateStep(
            "rules_corpus_verify",
            (
                python,
                "simctl.py",
                "rules",
                "verify",
                "--root",
                ".",
            ),
        ),
        GateStep(
            "architecture_policy",
            (
                python,
                "scripts/validate_architecture.py",
                "--check",
            ),
        ),
        GateStep(
            "documentation_policy",
            (
                python,
                "scripts/validate_documentation.py",
                "--check",
            ),
        ),
        GateStep(
            "focused_regressions",
            (
                python,
                "-m",
                "unittest",
                "-v",
                *focused_tests,
            ),
        ),
        GateStep(
            "four_player_natural_winner",
            (
                python,
                "-m",
                "unittest",
                "-v",
                "tests.test_deterministic_full_game",
            ),
        ),
        GateStep(
            "projection_and_privacy",
            (
                python,
                "-m",
                "unittest",
                "-v",
                *PRIVACY_TESTS,
            ),
        ),
        GateStep(
            "protocol_demo",
            (
                python,
                "scripts/demo_four_player_protocol.py",
                "--db",
                str(database),
                "--out",
                str(protocol_output),
            ),
        ),
        GateStep(
            "dependency_check",
            (
                python,
                "-m",
                "pip",
                "check",
            ),
        ),
        GateStep(
            "repository_security_validation",
            (
                python,
                "scripts/validate_repository.py",
            ),
        ),
        GateStep(
            "wheel_build",
            (
                python,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(wheel_output),
            ),
        ),
        GateStep(
            "wheel_clean_install",
            (
                python,
                "scripts/verify_wheel.py",
                "--dist",
                str(wheel_output),
            ),
        ),
        GateStep(
            "browser_dependencies",
            (npm, "ci", "--prefix", "web"),
        ),
        GateStep(
            "generated_protocol_types",
            (npm, "run", "generate:types", "--prefix", "web"),
        ),
        GateStep(
            "generated_protocol_freshness",
            ("git", "diff", "--exit-code", "--", "web/src/generated"),
        ),
        GateStep(
            "browser_production_build",
            (npm, "run", "build", "--prefix", "web"),
        ),
        GateStep(
            "browser_four_context_e2e",
            (npm, "run", "e2e", "--prefix", "web"),
        ),
    ]


def _write_summary(path: Path, summary: dict) -> None:
    path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def gate_environment(database: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["MTG_CARD_DB"] = str(database)
    environment["MTG_PYTHON_EXECUTABLE"] = str(Path(sys.executable).resolve())
    test_path = str(ROOT / "tests")
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        test_path
        if not existing_python_path
        else test_path + os.pathsep + existing_python_path
    )
    return environment


def _run_step(
    step: GateStep,
    *,
    number: int,
    output: Path,
    environment: dict[str, str],
) -> StepResult:
    log = output / f"{number:02d}-{step.name}.log"
    started_at = _utc_now()
    started = time.monotonic()
    print(f"[{number:02d}] {step.name}", flush=True)
    with log.open("w", encoding="utf-8", newline="\n") as stream:
        process = subprocess.Popen(
            list(step.command),
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            stream.write(line)
            stream.flush()
        returncode = process.wait()
    duration = round(time.monotonic() - started, 3)
    result = StepResult(
        name=step.name,
        command=list(step.command),
        started_at=started_at,
        completed_at=_utc_now(),
        duration_seconds=duration,
        returncode=returncode,
        log=_relative(log),
    )
    if returncode:
        raise subprocess.CalledProcessError(
            returncode,
            list(step.command),
        )
    print(f"     pass ({duration:.3f}s)", flush=True)
    return result


def _verify_protocol_output(output: Path) -> dict:
    return validate_protocol_output(output / "protocol-demo")


def _assert_clean(label: str) -> None:
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ValueError(f"Working tree is not clean {label}:\n{status}")


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete reproducible merge gate against the exact "
            "checked-out commit. Logs remain under ignored local/merge-gates/."
        )
    )
    parser.add_argument("--expected-branch", required=True)
    parser.add_argument("--expected-sha")
    parser.add_argument(
        "--card-db", type=Path, action="append",
        help="Full card snapshot for the fingerprint audit. Repeat for additional release snapshots.",
    )
    parser.add_argument(
        "--focused-test",
        action="append",
        default=[],
        help=(
            "Additional unittest module or fully qualified test ID. "
            "May be supplied more than once."
        ),
    )
    args = parser.parse_args()

    branch = _git("branch", "--show-current")
    sha = _git("rev-parse", "HEAD")
    expected_sha = args.expected_sha or sha
    if branch != args.expected_branch:
        raise SystemExit(
            f"Expected branch {args.expected_branch!r}, found {branch!r}"
        )
    if sha != expected_sha:
        raise SystemExit(f"Expected SHA {expected_sha}, found {sha}")
    _assert_clean("before merge gate")

    output = ROOT / "local" / "merge-gates" / sha
    output.mkdir(parents=True, exist_ok=True)
    database = output / "test-ci.sqlite3"
    wheel_output = output / "dist"
    wheel_output.mkdir(parents=True, exist_ok=True)
    for old_wheel in wheel_output.glob("quorune-*.whl"):
        old_wheel.unlink()

    focused_tests = tuple(
        dict.fromkeys((*DEFAULT_FOCUSED_TESTS, *args.focused_test))
    )
    python = str(Path(sys.executable).resolve())
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if npm is None:
        raise SystemExit("npm is required for the browser merge gate")
    environment = gate_environment(database)
    card_databases = args.card_db or [
        Path(os.environ.get("MTG_CARD_DB") or ROOT / "data/scryfall-current.sqlite3")
    ]
    steps = build_steps(
        python,
        database=database,
        output=output,
        focused_tests=focused_tests,
        npm=npm,
        card_databases=card_databases,
    )
    summary_path = output / "summary.json"
    summary: dict = {
        "schema_version": 1,
        "status": "running",
        "repository": _git("remote", "get-url", "origin"),
        "branch": branch,
        "sha": sha,
        "python": {
            "executable": python,
            "version": sys.version.split()[0],
            "platform": sys.platform,
        },
        "started_at": _utc_now(),
        "completed_at": None,
        "focused_tests": list(focused_tests),
        "steps": [],
        "protocol_demo": None,
        "final_worktree": None,
    }
    _write_summary(summary_path, summary)

    current_step: GateStep | None = None
    try:
        for number, step in enumerate(steps, start=1):
            current_step = step
            result = _run_step(
                step,
                number=number,
                output=output,
                environment=environment,
            )
            summary["steps"].append(asdict(result))
            _write_summary(summary_path, summary)
            if step.name == "protocol_demo":
                summary["protocol_demo"] = _verify_protocol_output(output)
                _write_summary(summary_path, summary)
        _assert_clean("after merge gate")
        summary["status"] = "pass"
        summary["final_worktree"] = "clean"
        returncode = 0
    except Exception as exc:
        summary["status"] = "fail"
        summary["failed_step"] = (
            current_step.name if current_step is not None else "preflight"
        )
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["final_worktree"] = _git(
            "status", "--porcelain=v1", "--untracked-files=all"
        )
        returncode = 1
    finally:
        summary["completed_at"] = _utc_now()
        _write_summary(summary_path, summary)

    print(
        json.dumps(
            {
                "status": summary["status"],
                "branch": branch,
                "sha": sha,
                "summary": _relative(summary_path),
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
