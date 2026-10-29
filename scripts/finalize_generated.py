from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generated_artifacts import (
    GeneratorSpec,
    all_outputs,
    check_command,
    load_manifest,
    topological_order,
    write_command,
)
from scripts.generated_finalization_receipt import (
    GeneratedFinalizationReceiptError,
    database_identity,
    verify_finalization_receipt,
    write_finalization_receipt,
)
from scripts.validate_python_runtime import require_supported_python


class GeneratedFinalizationError(RuntimeError):
    """Raised when generated outputs cannot be finalized deterministically."""


CommandRunner = Callable[[str, Sequence[str]], int]
POST_CHECKS = (
    ("architecture-policy", ("scripts/validate_architecture.py", "--check")),
    ("documentation-policy", ("scripts/validate_documentation.py", "--check")),
    ("diff-hygiene", ("git", "diff", "--check")),
)


def _display(command: Sequence[str]) -> str:
    return " ".join(command)


def _run_command(generator_id: str, command: Sequence[str]) -> int:
    print(f"[{generator_id}] {_display(command)}", flush=True)
    return subprocess.run(command, cwd=ROOT).returncode


def output_snapshot(
    specs: Sequence[GeneratorSpec], *, root: Path = ROOT
) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    for relative in all_outputs(specs):
        path = root / relative
        snapshot[relative] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else None
        )
    return snapshot


def _write_pass(
    specs: Sequence[GeneratorSpec],
    *,
    database: Path | None,
    include_manual: bool,
    selected_ids: frozenset[str] | None,
    runner: CommandRunner,
) -> tuple[str, ...]:
    executed: list[str] = []
    checked_transition = False
    for spec in topological_order(specs):
        if selected_ids is not None and spec.id not in selected_ids:
            continue
        command = write_command(
            spec,
            database=database,
            include_manual=include_manual,
        )
        if command is None:
            print(
                f"[{spec.id}] write skipped ({spec.write_policy})",
                flush=True,
            )
            continue
        if database is not None and spec.write_with_database is not None and not checked_transition:
            preflight = (str(Path(sys.executable).resolve()), 'scripts/update_rules_scheduler.py', '--validate-policy')
            code = runner('pre-corpus-semantic-transition', preflight)
            if code:
                raise GeneratedFinalizationError(f'pre-corpus semantic-transition policy failed ({code})')
            checked_transition = True
        returncode = runner(spec.id, command)
        if returncode:
            raise GeneratedFinalizationError(
                f"generated writer failed: {spec.id} ({returncode})"
            )
        executed.append(spec.id)
    return tuple(executed)


def stabilization_ids(
    specs: Sequence[GeneratorSpec], changed_outputs: Sequence[str]
) -> frozenset[str]:
    owner = {
        output: spec.id
        for spec in specs
        for output in spec.outputs
    }
    selected = {
        owner[output]
        for output in changed_outputs
        if output in owner
    }
    changed = True
    while changed:
        changed = False
        for spec in specs:
            if spec.id in selected or not selected.intersection(spec.depends_on):
                continue
            selected.add(spec.id)
            changed = True
    return frozenset(selected)


def write_until_stable(
    specs: Sequence[GeneratorSpec],
    *,
    database: Path | None,
    include_manual: bool,
    max_passes: int,
    initial_selected_ids: frozenset[str] | None = None,
    root: Path = ROOT,
    runner: CommandRunner = _run_command,
) -> dict[str, object]:
    if max_passes < 1:
        raise ValueError("max_passes must be positive")
    before = output_snapshot(specs, root=root)
    changed_by_pass: list[tuple[str, ...]] = []
    executed_by_pass: list[tuple[str, ...]] = []
    selected_ids = initial_selected_ids
    for pass_number in range(1, max_passes + 1):
        # A topological first pass already places every database-backed corpus
        # output before its declared consumers. Rebuilding the full corpus on
        # a stabilization pass adds minutes without improving dependency
        # closure. Later passes therefore rerun only changed owners and their
        # descendants, using each database generator's safe derived-only
        # writer when it has one.
        pass_database = database if pass_number == 1 else None
        executed = _write_pass(
            specs,
            database=pass_database,
            include_manual=include_manual,
            selected_ids=selected_ids,
            runner=runner,
        )
        after = output_snapshot(specs, root=root)
        changed = tuple(
            relative
            for relative in all_outputs(specs)
            if before.get(relative) != after.get(relative)
        )
        executed_by_pass.append(executed)
        changed_by_pass.append(changed)
        print(
            json.dumps(
                {
                    "generated_pass": pass_number,
                    "changed_outputs": list(changed),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if not changed:
            return {
                "passes": pass_number,
                "changed_by_pass": tuple(changed_by_pass),
                "executed_by_pass": tuple(executed_by_pass),
            }
        before = after
        selected_ids = stabilization_ids(specs, changed)
    raise GeneratedFinalizationError(
        "generated outputs did not reach a fixed point within "
        f"{max_passes} passes"
    )


def check_all(
    specs: Sequence[GeneratorSpec],
    *,
    runner: CommandRunner = _run_command,
) -> tuple[dict[str, object], ...]:
    failures: list[dict[str, object]] = []
    for spec in topological_order(specs):
        command = check_command(spec)
        returncode = runner(spec.id, command)
        if returncode:
            failures.append(
                {
                    "check": spec.id,
                    "command": list(command),
                    "returncode": returncode,
                }
            )
    for check_id, arguments in POST_CHECKS:
        command = (
            (str(Path(sys.executable).resolve()), *arguments)
            if arguments[0] != "git"
            else arguments
        )
        returncode = runner(check_id, command)
        if returncode:
            failures.append(
                {
                    "check": check_id,
                    "command": list(command),
                    "returncode": returncode,
                }
            )
    return tuple(failures)


def check_assembled(
    specs: Sequence[GeneratorSpec],
    *,
    runner: CommandRunner = _run_command,
) -> tuple[dict[str, object], ...]:
    """Check only boundaries not already certified by reusable receipts."""

    failures: list[dict[str, object]] = []
    for spec in topological_order(specs):
        if spec.reuse_policy == "safe":
            continue
        command = check_command(spec)
        returncode = runner(spec.id, command)
        if returncode:
            failures.append(
                {
                    "check": spec.id,
                    "command": list(command),
                    "returncode": returncode,
                }
            )
    for check_id, arguments in POST_CHECKS:
        command = (
            (str(Path(sys.executable).resolve()), *arguments)
            if arguments[0] != "git"
            else arguments
        )
        returncode = runner(check_id, command)
        if returncode:
            failures.append(
                {
                    "check": check_id,
                    "command": list(command),
                    "returncode": returncode,
                }
            )
    return tuple(failures)


def changed_generated_outputs(
    specs: Sequence[GeneratorSpec], *, root: Path = ROOT
) -> tuple[str, ...]:
    outputs = all_outputs(specs)
    if not outputs:
        return ()
    changed: set[str] = set()
    commands = (
        ("git", "diff", "--name-only", "--", *outputs),
        ("git", "diff", "--cached", "--name-only", "--", *outputs),
        (
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            *outputs,
        ),
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=root,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise GeneratedFinalizationError(
                "unable to inspect generated-output Git state: "
                + result.stderr.strip()
            )
        changed.update(
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        )
    return tuple(sorted(changed))


def _database(argument: str | None) -> Path | None:
    raw = argument or os.environ.get("MTG_CARD_DB")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser(
        description=(
            "Write or verify the complete discovered and registered "
            "generated-artifact set from one dependency-ordered manifest"
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument(
        "--assemble",
        action="store_true",
        help=(
            "Verify an already assembled dependency-ordered owner bundle and "
            "write its exact-head finalization receipt without rerunning owners"
        ),
    )
    mode.add_argument("--verify-receipt", action="store_true")
    parser.add_argument("--db")
    parser.add_argument("--include-manual", action="store_true")
    parser.add_argument("--max-passes", type=int, default=3)
    parser.add_argument(
        "--resume-from",
        metavar="GENERATOR_ID",
        help=(
            "After a failed write, rerun one registered generator and its "
            "descendants; every freshness and policy check still runs"
        ),
    )
    parser.add_argument(
        "--fail-on-change",
        action="store_true",
        help=(
            "After writing, fail when generated outputs differ from the Git "
            "index so a pre-push hook cannot silently omit them."
        ),
    )
    args = parser.parse_args()
    if args.resume_from and not args.write:
        parser.error("--resume-from requires --write")
    database = _database(args.db)
    if args.write:
        mode_name = "write"
    elif args.assemble:
        mode_name = "assemble"
    elif args.verify_receipt:
        mode_name = "receipt"
    else:
        mode_name = "check"
    result: dict[str, object] = {
        "ok": False,
        "mode": mode_name,
        "manifest": "platform/generated-artifacts.json",
        "generator_count": None,
        "database": str(database) if database is not None else None,
    }
    try:
        specs = load_manifest()
        result["generator_count"] = len(specs)
        if args.verify_receipt:
            receipt_path, receipt = verify_finalization_receipt(
                specs,
                database=database,
                root=ROOT,
            )
            result["database"] = receipt.database_path
            result["receipt"] = {
                "path": str(receipt_path),
                "source_tree_fingerprint": (
                    receipt.source_tree_fingerprint
                ),
                "generated_outputs_fingerprint": (
                    receipt.generated_outputs_fingerprint
                ),
            }
            result["ok"] = True
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        initial_database_identity = (
            database_identity(database) if (args.write or args.assemble) else None
        )
        selected_ids: frozenset[str] | None = None
        if args.resume_from:
            resumed = next(
                (spec for spec in specs if spec.id == args.resume_from),
                None,
            )
            if resumed is None:
                raise ValueError(
                    f"Unknown generated-artifact owner: {args.resume_from}"
                )
            selected_ids = stabilization_ids(specs, resumed.outputs)
            result["resume_from"] = args.resume_from
        if args.write:
            result["write"] = write_until_stable(
                specs,
                database=database,
                include_manual=args.include_manual,
                max_passes=args.max_passes,
                initial_selected_ids=selected_ids,
            )
        if args.assemble:
            from scripts.cloud_generated_artifacts import (
                _owner_receipt_inventory,
            )

            inventory = _owner_receipt_inventory(specs, required=True)
            result["owner_receipts"] = inventory
            failures = check_assembled(specs)
        else:
            failures = check_all(specs)
        if failures:
            result["failures"] = failures
            print(json.dumps(result, indent=2, sort_keys=True))
            return 1
        changed = changed_generated_outputs(specs) if args.fail_on_change else ()
        if changed:
            result["changed_generated_outputs"] = changed
            print(json.dumps(result, indent=2, sort_keys=True))
            print(
                "Generated outputs changed during finalization. Inspect and "
                "commit them, then retry the push.",
                file=sys.stderr,
            )
            return 1
        if args.write or args.assemble:
            receipt_path, receipt = write_finalization_receipt(
                specs,
                database=database,
                root=ROOT,
                expected_database_identity=initial_database_identity,
            )
            result["receipt"] = {
                "path": str(receipt_path),
                "source_tree_fingerprint": (
                    receipt.source_tree_fingerprint
                ),
                "generated_outputs_fingerprint": (
                    receipt.generated_outputs_fingerprint
                ),
            }
    except (
        GeneratedFinalizationError,
        GeneratedFinalizationReceiptError,
        OSError,
        ValueError,
    ) as exc:
        result["error"] = str(exc)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    result["ok"] = True
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
