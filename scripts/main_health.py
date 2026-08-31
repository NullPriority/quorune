from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MAIN_BROAD_WORKFLOW = ".github/workflows/main-broad.yml"
RECOVERY_LABEL = "main-red-recovery"
FIX_FORWARD_LABEL = "main-red-fix-forward"
RECOVERY_INCIDENT_SOURCE = "platform/ci-escape-source.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PYTHON_JOB = re.compile(
    r"^Main / Broad / Python / ubuntu / (?P<suite>[a-z0-9-]+)$"
)
_BROWSER_JOB = re.compile(
    r"^Main / Broad / Browser / (?P<group>[a-z0-9-]+)$"
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


@dataclass(frozen=True, slots=True)
class FailedBrowserJob:
    job_id: int
    name: str
    group: str


@dataclass(frozen=True, slots=True)
class BrowserFailure:
    group: str
    path: str
    title: str
    identifier: str
    harness_paths: tuple[str, ...]


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


def _request_label(event: Any, expected: str) -> bool:
    pull_request = event.get("pull_request") if isinstance(event, Mapping) else None
    labels = pull_request.get("labels", []) if isinstance(pull_request, Mapping) else []
    return any(
        isinstance(item, Mapping)
        and str(item.get("name", "")).casefold() == expected.casefold()
        for item in labels
    )


def recovery_requested(event: Any) -> bool:
    return _request_label(event, RECOVERY_LABEL)


def fix_forward_requested(event: Any) -> bool:
    return _request_label(event, FIX_FORWARD_LABEL)


def verify_main_health(
    value: Any,
    *,
    allow_recovery: bool,
    allow_fix_forward: bool = False,
) -> dict[str, Any]:
    if allow_recovery and allow_fix_forward:
        raise MainHealthError("recovery and fix-forward labels are mutually exclusive")
    latest = latest_completed_main_broad(value)
    if latest is None:
        if allow_recovery or allow_fix_forward:
            raise MainHealthError(
                "main-red labels cannot be used before main broad exists"
            )
        return {"state": "bootstrap", "workflow_run_id": None}
    conclusion = latest.get("conclusion")
    if conclusion == "success":
        if allow_recovery or allow_fix_forward:
            raise MainHealthError("main-red labels cannot be used while main is green")
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
    if allow_fix_forward:
        return {
            "state": "red-fix-forward-requested",
            "workflow_run_id": latest["id"],
        }
    raise MainHealthError(
        "latest completed Main / Broad regression is red; automatic merges are "
        f"blocked by workflow run {latest['id']}. Apply {RECOVERY_LABEL!r} only "
        "to a provenance-verifiable nonsemantic fixture or harness fix, or "
        f"{FIX_FORWARD_LABEL!r} for a complete high-risk fix-forward."
    )


def _failed_main_recovery_jobs(
    value: Any,
) -> tuple[tuple[FailedPythonJob, ...], tuple[FailedBrowserJob, ...], tuple[str, ...]]:
    if not isinstance(value, Mapping) or not isinstance(value.get("jobs"), list):
        raise MainHealthError("main broad workflow-job response is malformed")
    python: list[FailedPythonJob] = []
    browser: list[FailedBrowserJob] = []
    unexpected: list[str] = []
    for row in value["jobs"]:
        if not isinstance(row, Mapping):
            raise MainHealthError("main broad workflow job must be an object")
        if row.get("conclusion") != "failure":
            continue
        name = str(row.get("name") or "")
        python_match = _PYTHON_JOB.fullmatch(name)
        browser_match = _BROWSER_JOB.fullmatch(name)
        if python_match or browser_match:
            job_id = row.get("id")
            if type(job_id) is not int or job_id <= 0:
                raise MainHealthError("failed recovery job has no immutable job id")
            if python_match:
                python.append(
                    FailedPythonJob(job_id, name, python_match.group("suite"))
                )
            else:
                assert browser_match is not None
                browser.append(
                    FailedBrowserJob(job_id, name, browser_match.group("group"))
                )
        elif name not in _DERIVED_FAILURE_JOBS:
            unexpected.append(name or "<unnamed>")
    return (
        tuple(sorted(python, key=lambda job: job.suite)),
        tuple(sorted(browser, key=lambda job: job.group)),
        tuple(sorted(unexpected)),
    )


def failed_main_python_jobs(value: Any) -> tuple[FailedPythonJob, ...]:
    failed, browser, unexpected = _failed_main_recovery_jobs(value)
    unexpected = (*unexpected, *(job.name for job in browser))
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
    return failed


def failed_main_recovery_jobs(
    value: Any,
) -> tuple[tuple[FailedPythonJob, ...], tuple[FailedBrowserJob, ...]]:
    python, browser, unexpected = _failed_main_recovery_jobs(value)
    if unexpected:
        raise MainHealthError(
            "main-red recovery cannot narrow unsupported failures: "
            + ", ".join(unexpected)
        )
    if not python and not browser:
        raise MainHealthError(
            "main-red recovery has no provenance-bearing failure"
        )
    if len({job.suite for job in python}) != len(python):
        raise MainHealthError("main broad has duplicate failed Python shard jobs")
    if len({job.group for job in browser}) != len(browser):
        raise MainHealthError("main broad has duplicate failed browser group jobs")
    return python, browser


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


def _json_documents(directory: Path) -> tuple[tuple[Path, dict[str, Any]], ...]:
    documents: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise MainHealthError(f"recovery artifact {path} must be an object")
        documents.append((path, value))
    return tuple(documents)


def _browser_test_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise MainHealthError("browser recovery report has no test file")
    normalized = value.replace("\\", "/")
    marker = "/web/tests/"
    if marker in normalized:
        normalized = normalized.split(marker, 1)[1]
    elif normalized.startswith("web/tests/"):
        normalized = normalized.removeprefix("web/tests/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.suffix not in {".ts", ".tsx", ".js", ".jsx"}
    ):
        raise MainHealthError(f"browser recovery test path is invalid: {value!r}")
    return f"web/tests/{path.as_posix()}"


def _browser_error_paths(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ()
    candidates: set[str] = set()
    location = value.get("location")
    if isinstance(location, Mapping):
        file_value = location.get("file")
        if isinstance(file_value, str) and "/web/tests/" in file_value.replace(
            "\\", "/"
        ):
            candidates.add(_browser_test_path(file_value))
    for field in ("stack", "message"):
        text = value.get(field)
        if not isinstance(text, str):
            continue
        for match in re.finditer(
            r"(?:^|[/\\])web[/\\]tests[/\\](?P<path>[^:\n\r]+?\.(?:tsx?|jsx?))",
            text,
        ):
            candidates.add(_browser_test_path(match.group("path")))
    return tuple(sorted(candidates))


def _browser_failure_identifier(path: str, title: str) -> str:
    return f"{path}::{title}"


def _browser_focus_pattern(title: str) -> str:
    special = frozenset("\\.^$|?*+()[]{}")
    escaped = "".join(f"\\{char}" if char in special else char for char in title)
    return f"^{escaped}$"


def browser_failures_from_reports(
    *,
    jobs: Sequence[FailedBrowserJob],
    documents: Sequence[tuple[Path, Mapping[str, Any]]],
    main_head: str,
) -> tuple[BrowserFailure, ...]:
    failures: list[BrowserFailure] = []
    for job in jobs:
        filename = f"main-broad-{job.group}.json"
        matches = [item for item in documents if item[0].name == filename]
        if len(matches) != 1:
            raise MainHealthError(
                f"failed browser job {job.job_id} has no unique Playwright report"
            )
        _, report = matches[0]
        config = report.get("config")
        metadata = config.get("metadata") if isinstance(config, Mapping) else None
        ci = metadata.get("ci") if isinstance(metadata, Mapping) else None
        if not isinstance(ci, Mapping) or ci.get("commitHash") != main_head:
            raise MainHealthError(
                f"failed browser job {job.job_id} report is bound to another source"
            )
        stats = report.get("stats")
        if (
            not isinstance(stats, Mapping)
            or type(stats.get("unexpected")) is not int
            or int(stats["unexpected"]) <= 0
        ):
            raise MainHealthError(
                f"failed browser job {job.job_id} report has no unexpected test"
            )
        job_failures: list[BrowserFailure] = []
        suites = report.get("suites")
        if not isinstance(suites, list):
            raise MainHealthError(
                f"failed browser job {job.job_id} report suites are malformed"
            )
        for suite in suites:
            if not isinstance(suite, Mapping):
                raise MainHealthError("Playwright suite must be an object")
            specs = suite.get("specs")
            if not isinstance(specs, list):
                raise MainHealthError("Playwright suite specs are malformed")
            for spec in specs:
                if not isinstance(spec, Mapping) or spec.get("ok") is not False:
                    continue
                tests = spec.get("tests")
                unexpected = [
                    test
                    for test in tests
                    if isinstance(test, Mapping)
                    and test.get("status") == "unexpected"
                ] if isinstance(tests, list) else []
                if not unexpected:
                    continue
                title = spec.get("title")
                if not isinstance(title, str) or not title:
                    raise MainHealthError("failed Playwright spec has no title")
                path = _browser_test_path(spec.get("file") or suite.get("file"))
                harness_paths = {path}
                for test in unexpected:
                    results = test.get("results")
                    if not isinstance(results, list):
                        raise MainHealthError(
                            "failed Playwright test results are malformed"
                        )
                    for result in results:
                        if not isinstance(result, Mapping):
                            continue
                        harness_paths.update(_browser_error_paths(result.get("error")))
                        errors = result.get("errors")
                        if isinstance(errors, list):
                            for error in errors:
                                harness_paths.update(_browser_error_paths(error))
                job_failures.append(
                    BrowserFailure(
                        group=job.group,
                        path=path,
                        title=title,
                        identifier=_browser_failure_identifier(path, title),
                        harness_paths=tuple(sorted(harness_paths)),
                    )
                )
        if not job_failures or len(job_failures) != int(stats["unexpected"]):
            raise MainHealthError(
                f"failed browser job {job.job_id} report failure count is ambiguous"
            )
        failures.extend(job_failures)
    identifiers = [failure.identifier for failure in failures]
    if len(identifiers) != len(set(identifiers)):
        raise MainHealthError("browser recovery reports duplicate failed tests")
    return tuple(sorted(failures, key=lambda item: item.identifier))


def _browser_test_suffix(source: str, *, path: str) -> str:
    match = re.search(r"(?m)^test\(", source)
    if match is None:
        raise MainHealthError(f"browser recovery file has no top-level test: {path}")
    return source[match.start():]


def validate_recovery_browser_changes(
    *,
    base_sources: Mapping[str, str],
    head_sources: Mapping[str, str],
) -> tuple[str, ...]:
    if set(base_sources) != set(head_sources) or not base_sources:
        raise MainHealthError("browser recovery source inventory is incomplete")
    for path in sorted(base_sources):
        if _browser_test_suffix(
            base_sources[path], path=path
        ) != _browser_test_suffix(head_sources[path], path=path):
            raise MainHealthError(
                f"browser recovery may not change tests, tags, or assertions: {path}"
            )
    return tuple(sorted(base_sources))


def validate_recovery_incident_source(
    *,
    base_source: str,
    head_source: str,
    main_run_id: int,
    main_head: str,
) -> None:
    before = json.loads(base_source)
    after = json.loads(head_source)
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise MainHealthError("recovery incident source must be an object")
    stable_fields = set(before) - {"observed_at", "escapes"}
    if set(after) != set(before) or any(
        before[field] != after[field] for field in stable_fields
    ):
        raise MainHealthError("recovery incident source changed unrelated fields")
    before_escapes = before.get("escapes")
    after_escapes = after.get("escapes")
    if (
        not isinstance(before_escapes, list)
        or not isinstance(after_escapes, list)
        or after_escapes[:-1] != before_escapes
        or len(after_escapes) != len(before_escapes) + 1
    ):
        raise MainHealthError("recovery must append exactly one CI escape record")
    incident = after_escapes[-1]
    if (
        not isinstance(incident, Mapping)
        or incident.get("run_id") != main_run_id
        or incident.get("head_sha") != main_head
        or incident.get("deterministic") is not True
    ):
        raise MainHealthError(
            "recovery incident record does not bind the failed main transition"
        )


def _generated_outputs_for_recovery(
    *, root: Path, base_ref: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    from scripts.generated_artifacts import load_manifest
    from scripts.generated_owner_cache import affected_owner_plan

    plan = affected_owner_plan(base_ref=base_ref, root=root)
    if plan.get("reason") != "declared-input-change":
        raise MainHealthError("generated-owner recovery plan failed closed")
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
    automatic = tuple(
        owner for owner in owners if by_id[owner].write_policy == "automatic"
    )
    outputs = {output for owner in automatic for output in by_id[owner].outputs}
    return automatic, tuple(sorted(outputs))


def verify_recovery_route(
    *,
    runs: Any,
    jobs: Any,
    results_dir: Path,
    base_sha: str,
    head_sha: str,
    root: Path = ROOT,
) -> dict[str, Any]:
    from scripts.shard_result_validation import validate_result_document

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
    python_jobs, browser_jobs = failed_main_recovery_jobs(jobs)
    documents = _json_documents(results_dir)
    python_documents = [
        document
        for _, document in documents
        if document.get("type") == "python-shard-result"
    ]
    failed_test_ids: set[str] = set()
    error_test_ids: set[str] = set()
    for job in python_jobs:
        matches = [
            document
            for document in python_documents
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
    python_identifiers = tuple(sorted(failed_test_ids | error_test_ids))
    modules = tuple(
        sorted(
            {
                identifier.split(".", 2)[1]
                for identifier in python_identifiers
                if identifier.startswith("tests.test_")
            }
        )
    )
    if python_jobs and (not modules or any(
        not identifier.startswith(tuple(f"tests.{module}." for module in modules))
        for identifier in python_identifiers
    )):
        raise MainHealthError("failed shard artifact has invalid canonical test ids")
    browser_failures = browser_failures_from_reports(
        jobs=browser_jobs,
        documents=documents,
        main_head=main_head,
    )
    browser_identifiers = tuple(
        failure.identifier for failure in browser_failures
    )
    identifiers = tuple(sorted((*python_identifiers, *browser_identifiers)))
    test_paths = {f"tests/{module}.py" for module in modules}
    browser_test_paths = {failure.path for failure in browser_failures}
    browser_harness_paths = {
        path
        for failure in browser_failures
        for path in failure.harness_paths
    }
    owners, generated_outputs = _generated_outputs_for_recovery(
        root=root, base_ref=base_sha
    )
    changed = set(_changed_paths(root, base_sha, head_sha))
    allowed = (
        test_paths
        | browser_harness_paths
        | {RECOVERY_INCIDENT_SOURCE}
        | set(generated_outputs)
    )
    directly_responsible = test_paths | browser_harness_paths
    if (
        not changed
        or not changed.issubset(allowed)
        or not changed & directly_responsible
    ):
        unexpected = sorted(changed - allowed)
        raise MainHealthError(
            "recovery changed files outside failed tests, harnesses, incidents, "
            "and their generated owners: "
            + ", ".join(unexpected or sorted(changed))
        )
    changed_modules = tuple(sorted(Path(path).stem for path in changed & test_paths))
    if changed_modules:
        validate_recovery_test_changes(
            base_sources={
                module: _source_at_ref(root, base_sha, f"tests/{module}.py")
                for module in changed_modules
            },
            head_sources={
                module: (root / "tests" / f"{module}.py").read_text(
                    encoding="utf-8"
                )
                for module in changed_modules
            },
            failed_test_ids=python_identifiers,
        )
    changed_browser_tests = tuple(sorted(changed & browser_test_paths))
    if changed_browser_tests:
        validate_recovery_browser_changes(
            base_sources={
                path: _source_at_ref(root, base_sha, path)
                for path in changed_browser_tests
            },
            head_sources={
                path: (root / path).read_text(encoding="utf-8")
                for path in changed_browser_tests
            },
        )
    if RECOVERY_INCIDENT_SOURCE in changed:
        validate_recovery_incident_source(
            base_source=_source_at_ref(root, base_sha, RECOVERY_INCIDENT_SOURCE),
            head_source=(root / RECOVERY_INCIDENT_SOURCE).read_text(
                encoding="utf-8"
            ),
            main_run_id=int(latest["id"]),
            main_head=main_head,
        )
    return {
        "schema_version": 2,
        "main_run_id": int(latest["id"]),
        "main_head_sha": main_head,
        "failed_jobs": [
            {"id": job.job_id, "kind": "python", "name": job.name, "suite": job.suite}
            for job in python_jobs
        ] + [
            {"id": job.job_id, "kind": "browser", "name": job.name, "group": job.group}
            for job in browser_jobs
        ],
        "failed_test_ids": list(identifiers),
        "test_modules": list(changed_modules),
        "browser_test_files": sorted(browser_test_paths),
        "browser_focus_patterns": sorted(
            {_browser_focus_pattern(failure.title) for failure in browser_failures}
        ),
        "changed_files": sorted(changed),
        "generated_owners": list(owners),
        "generated_outputs": list(generated_outputs),
    }


def _write_outputs(path: Path, values: Mapping[str, str]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def verify_fix_forward_base(*, runs: Any, event: Any) -> str:
    latest = latest_completed_main_broad(runs)
    pull_request = event.get("pull_request") if isinstance(event, Mapping) else None
    base = pull_request.get("base") if isinstance(pull_request, Mapping) else None
    base_sha = base.get("sha") if isinstance(base, Mapping) else None
    main_head = latest.get("head_sha") if isinstance(latest, Mapping) else None
    if (
        not isinstance(base_sha, str)
        or not _SHA.fullmatch(base_sha)
        or not isinstance(main_head, str)
        or not _SHA.fullmatch(main_head)
        or base_sha != main_head
    ):
        raise MainHealthError(
            "high-risk fix-forward base does not exactly match failed main"
        )
    return base_sha


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
        fix_forward = fix_forward_requested(event)
        state = verify_main_health(
            runs,
            allow_recovery=requested,
            allow_fix_forward=fix_forward,
        )
        output = {
            "ok": True,
            **state,
            "recovery_requested": requested,
            "fix_forward_requested": fix_forward,
        }
        if state["state"] == "red-fix-forward-requested":
            output["fix_forward_base_sha"] = verify_fix_forward_base(
                runs=runs, event=event
            )
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
                "force_high_risk": str(
                    state["state"] == "red-fix-forward-requested"
                ).lower(),
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
