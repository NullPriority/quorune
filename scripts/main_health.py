from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
MAIN_BROAD_WORKFLOW = ".github/workflows/main-broad.yml"
RECOVERY_LABEL = "main-red-recovery"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PYTHON_JOB = re.compile(
    r"^Main / Broad / Python / ubuntu / (?P<suite>[a-z0-9-]+)$"
)
_DERIVED_FAILURE_JOBS = frozenset(
    {"Main / Broad regression", "Main / Broad / Halt auto-merge"}
)


class MainHealthError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FailedPythonJob:
    job_id: int
    name: str
    suite: str


def latest_completed_main_broad(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or not isinstance(
        value.get("workflow_runs"), list
    ):
        raise MainHealthError("main broad workflow-run response is malformed")
    candidates = [
        row
        for row in value["workflow_runs"]
        if isinstance(row, Mapping)
        and row.get("path") == MAIN_BROAD_WORKFLOW
        and row.get("event") == "push"
        and row.get("status") == "completed"
        and row.get("head_branch", "main") == "main"
        and type(row.get("id")) is int
    ]
    return max(candidates, key=lambda row: int(row["id"])) if candidates else None


def recovery_requested(event: Any) -> bool:
    pull_request = event.get("pull_request") if isinstance(event, Mapping) else None
    labels = pull_request.get("labels", []) if isinstance(pull_request, Mapping) else []
    return any(
        isinstance(label, Mapping)
        and str(label.get("name", "")).casefold() == RECOVERY_LABEL
        for label in labels
    )


def verify_main_health(value: Any, *, allow_recovery: bool) -> dict[str, Any]:
    latest = latest_completed_main_broad(value)
    if latest is None:
        if allow_recovery:
            raise MainHealthError("recovery label cannot be used before main broad exists")
        return {"state": "bootstrap", "workflow_run_id": None}
    conclusion = latest.get("conclusion")
    if conclusion == "success":
        if allow_recovery:
            raise MainHealthError("recovery label cannot be used while main is green")
        return {"state": "green", "workflow_run_id": latest["id"]}
    if conclusion != "failure":
        raise MainHealthError(
            "latest completed Main / Broad regression has an unsupported conclusion"
        )
    if allow_recovery:
        return {
            "state": "red-recovery-requested",
            "workflow_run_id": latest["id"],
        }
    raise MainHealthError(
        "latest completed Main / Broad regression is red; automatic merges are "
        f"blocked by workflow run {latest['id']}. Apply {RECOVERY_LABEL!r} only "
        "to a provenance-verifiable test-fixture or harness fix."
    )


def failed_main_python_jobs(value: Any) -> tuple[FailedPythonJob, ...]:
    if not isinstance(value, Mapping) or not isinstance(value.get("jobs"), list):
        raise MainHealthError("main broad workflow-job response is malformed")
    failed: list[FailedPythonJob] = []
    unexpected: list[str] = []
    for row in value["jobs"]:
        if not isinstance(row, Mapping):
            raise MainHealthError("main broad workflow job must be an object")
        if row.get("conclusion") != "failure":
            continue
        name = str(row.get("name") or "")
        match = _PYTHON_JOB.fullmatch(name)
        if match:
            job_id = row.get("id")
            if type(job_id) is not int or job_id <= 0:
                raise MainHealthError("failed Python job has no immutable job id")
            failed.append(FailedPythonJob(job_id, name, match.group("suite")))
        elif name not in _DERIVED_FAILURE_JOBS:
            unexpected.append(name or "<unnamed>")
    if unexpected:
        raise MainHealthError(
            "main-red recovery cannot narrow non-Python failures: "
            + ", ".join(sorted(unexpected))
        )
    if not failed:
        raise MainHealthError(
            "main-red recovery has no provenance-bearing Ubuntu Python failure"
        )
    if len({job.suite for job in failed}) != len(failed):
        raise MainHealthError("main broad has duplicate failed Python shard jobs")
    return tuple(sorted(failed, key=lambda job: job.suite))


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise MainHealthError(result.stderr.strip() or "Git inspection failed")
    return result.stdout


def _changed_paths(root: Path, base: str, head: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                line.strip().replace("\\", "/")
                for line in _git(root, "diff", "--name-only", base, head).splitlines()
                if line.strip()
            }
        )
    )


def _source_at_ref(root: Path, ref: str, path: str) -> str:
    return _git(root, "show", f"{ref}:{path}")


def _qualified_tests(source: str, *, path: str) -> dict[str, ast.AST]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise MainHealthError(f"recovery test module is invalid: {path}") from exc
    result: dict[str, ast.AST] = {}
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test")
        ):
            result[node.name] = node
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name.startswith("test")
            ):
                result[f"{node.name}.{child.name}"] = child
    return result


def _decorators(node: ast.AST) -> tuple[str, ...]:
    values = getattr(node, "decorator_list", [])
    return tuple(ast.dump(value, include_attributes=False) for value in values)


def _assertions(node: ast.AST) -> tuple[str, ...]:
    assertions: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            assertions.append(ast.dump(child, include_attributes=False))
        elif (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr.startswith("assert")
        ):
            assertions.append(ast.dump(child, include_attributes=False))
    return tuple(sorted(assertions))


def _test_key(identifier: str, *, module: str) -> str:
    prefix = f"tests.{module}."
    if not identifier.startswith(prefix):
        raise MainHealthError(
            f"failed test id {identifier!r} does not belong to {module}"
        )
    key = identifier.removeprefix(prefix)
    if not key:
        raise MainHealthError(f"failed test id {identifier!r} is incomplete")
    return key


def validate_recovery_test_changes(
    *,
    base_sources: Mapping[str, str],
    head_sources: Mapping[str, str],
    failed_test_ids: Sequence[str],
) -> tuple[str, ...]:
    if set(base_sources) != set(head_sources) or not base_sources:
        raise MainHealthError("recovery test source inventory is incomplete")
    changed_modules: list[str] = []
    for module in sorted(base_sources):
        path = f"tests/{module}.py"
        before = _qualified_tests(base_sources[module], path=path)
        after = _qualified_tests(head_sources[module], path=path)
        if set(before) != set(after):
            raise MainHealthError(
                f"recovery may not add, remove, or rename tests in {path}"
            )
        failing = {
            _test_key(identifier, module=module)
            for identifier in failed_test_ids
            if identifier.startswith(f"tests.{module}.")
        }
        if not failing:
            raise MainHealthError(
                f"changed recovery module was not reported failing: {path}"
            )
        for key in sorted(before):
            if _decorators(before[key]) != _decorators(after[key]):
                raise MainHealthError(
                    f"recovery may not change test decorators: {module}.{key}"
                )
            if key not in failing and ast.dump(
                before[key], include_attributes=False
            ) != ast.dump(after[key], include_attributes=False):
                raise MainHealthError(
                    f"recovery changed a nonfailing test: {module}.{key}"
                )
            if key in failing and _assertions(before[key]) != _assertions(
                after[key]
            ):
                raise MainHealthError(
                    f"recovery weakened or changed assertions: {module}.{key}"
                )
        changed_modules.append(module)
    return tuple(changed_modules)


def _generated_outputs_for_recovery(
    *, root: Path, base_ref: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    from scripts.generated_artifacts import load_manifest
    from scripts.generated_owner_cache import affected_owner_plan

    plan = affected_owner_plan(base_ref=base_ref, root=root)
    if plan.get("reason") != "declared-input-change":
        raise MainHealthError("generated-owner recovery plan failed closed")
    if plan.get("database_required"):
        raise MainHealthError(
            "main-red recovery cannot execute database-backed compiler census"
        )
    owners = plan.get("owners")
    if not isinstance(owners, list) or any(
        not isinstance(item, str) for item in owners
    ):
        raise MainHealthError("generated-owner recovery plan is malformed")
    by_id = {
        spec.id: spec
        for spec in load_manifest(
            root / "platform/generated-artifacts.json", root=root
        )
    }
    if not set(owners).issubset(by_id):
        raise MainHealthError("generated-owner recovery plan names unknown owners")
    outputs = {output for owner in owners for output in by_id[owner].outputs}
    return tuple(owners), tuple(sorted(outputs))


def verify_recovery_route(
    *,
    runs: Any,
    jobs: Any,
    results_dir: Path,
    base_sha: str,
    head_sha: str,
    root: Path = ROOT,
) -> dict[str, Any]:
    from scripts.shard_result_validation import (
        result_documents,
        validate_result_document,
    )

    latest = latest_completed_main_broad(runs)
    if latest is None or latest.get("conclusion") != "failure":
        raise MainHealthError("recovery requires the latest failed main broad run")
    main_head = latest.get("head_sha")
    if (
        not isinstance(main_head, str)
        or not _SHA.fullmatch(main_head)
        or main_head != base_sha
    ):
        raise MainHealthError(
            "recovery branch base does not exactly match the failed main source tree"
        )
    if not _SHA.fullmatch(head_sha):
        raise MainHealthError("recovery head must be a full Git SHA")
    failed_jobs = failed_main_python_jobs(jobs)
    documents = result_documents(results_dir)
    failed_test_ids: set[str] = set()
    error_test_ids: set[str] = set()
    for job in failed_jobs:
        matches = [
            document
            for document in documents
            if document.get("platform") == "ubuntu"
            and document.get("suite") == job.suite
            and document.get("successful") is False
        ]
        if len(matches) != 1:
            raise MainHealthError(
                f"failed job {job.job_id} has no unique shard result artifact"
            )
        try:
            document = validate_result_document(
                matches[0],
                expected_suite=job.suite,
                expected_platform="ubuntu",
                expected_backend="pytest-xdist",
                require_success=False,
            )
        except ValueError as exc:
            raise MainHealthError(
                f"failed job {job.job_id} artifact is invalid: {exc}"
            ) from exc
        failed_test_ids.update(document["failed_test_ids"])
        error_test_ids.update(document["error_test_ids"])
    identifiers = tuple(sorted(failed_test_ids | error_test_ids))
    modules = tuple(
        sorted(
            {
                identifier.split(".", 2)[1]
                for identifier in identifiers
                if identifier.startswith("tests.test_")
            }
        )
    )
    if not modules or any(
        not identifier.startswith(tuple(f"tests.{module}." for module in modules))
        for identifier in identifiers
    ):
        raise MainHealthError("failed shard artifact has invalid canonical test ids")
    test_paths = {f"tests/{module}.py" for module in modules}
    owners, generated_outputs = _generated_outputs_for_recovery(
        root=root, base_ref=base_sha
    )
    changed = set(_changed_paths(root, base_sha, head_sha))
    allowed = test_paths | set(generated_outputs)
    if not changed or not changed.issubset(allowed) or not changed & test_paths:
        unexpected = sorted(changed - allowed)
        raise MainHealthError(
            "recovery changed files outside failed tests and their generated owners: "
            + ", ".join(unexpected or sorted(changed))
        )
    changed_modules = tuple(sorted(Path(path).stem for path in changed & test_paths))
    validate_recovery_test_changes(
        base_sources={
            module: _source_at_ref(root, base_sha, f"tests/{module}.py")
            for module in changed_modules
        },
        head_sources={
            module: (root / "tests" / f"{module}.py").read_text(encoding="utf-8")
            for module in changed_modules
        },
        failed_test_ids=identifiers,
    )
    return {
        "schema_version": 1,
        "main_run_id": int(latest["id"]),
        "main_head_sha": main_head,
        "failed_jobs": [
            {"id": job.job_id, "name": job.name, "suite": job.suite}
            for job in failed_jobs
        ],
        "failed_test_ids": list(identifiers),
        "test_modules": list(changed_modules),
        "changed_files": sorted(changed),
        "generated_owners": list(owners),
        "generated_outputs": list(generated_outputs),
    }


def _write_outputs(path: Path, values: Mapping[str, str]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Block red main or prove a narrow nonsemantic recovery"
    )
    parser.add_argument("--runs-json", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--github-output")
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument("--jobs-json")
    parser.add_argument("--results-dir")
    parser.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    args = parser.parse_args()
    try:
        runs = json.loads(Path(args.runs_json).read_text(encoding="utf-8"))
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        requested = recovery_requested(event)
        state = verify_main_health(runs, allow_recovery=requested)
        output = {"ok": True, **state, "recovery_requested": requested}
        if state["state"] == "red-recovery-requested" and not args.discovery_only:
            if not all((args.jobs_json, args.results_dir, args.base_sha, args.head_sha)):
                raise MainHealthError(
                    "recovery verification requires jobs, results, base, and head"
                )
            plan = verify_recovery_route(
                runs=runs,
                jobs=json.loads(Path(args.jobs_json).read_text(encoding="utf-8")),
                results_dir=Path(args.results_dir),
                base_sha=args.base_sha,
                head_sha=args.head_sha,
            )
            output["recovery_plan"] = plan
        if args.github_output:
            values = {
                "main_state": str(output["state"]),
                "main_run_id": str(output["workflow_run_id"] or ""),
            }
            if "recovery_plan" in output:
                values["recovery_plan_json"] = json.dumps(
                    output["recovery_plan"], separators=(",", ":"), sort_keys=True
                )
            _write_outputs(Path(args.github_output), values)
    except (OSError, json.JSONDecodeError, MainHealthError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
