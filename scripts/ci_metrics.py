from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _duration(start: str | None, end: str | None) -> float | None:
    started = _time(start)
    completed = _time(end)
    if started is None or completed is None:
        return None
    return round((completed - started).total_seconds(), 3)


def _step_rows(job: Mapping[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps") or []
    if not isinstance(steps, Sequence):
        raise ValueError("Job steps must be a list")
    rows: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, Mapping):
            raise ValueError("Every job step must be an object")
        rows.append(
            {
                "name": str(step.get("name") or "unknown"),
                "conclusion": step.get("conclusion"),
                "duration_seconds": _duration(
                    step.get("started_at"), step.get("completed_at")
                ),
            }
        )
    return rows


def _specs(suites: Iterable[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    for suite in suites:
        specs = suite.get("specs") or []
        if not isinstance(specs, Sequence):
            raise ValueError("Playwright suite specs must be a list")
        for spec in specs:
            if not isinstance(spec, Mapping):
                raise ValueError("Every Playwright spec must be an object")
            yield spec
        children = suite.get("suites") or []
        if not isinstance(children, Sequence):
            raise ValueError("Nested Playwright suites must be a list")
        yield from _specs(
            child
            for child in children
            if isinstance(child, Mapping)
        )


def _failure_classification(status: str, errors: Sequence[Any]) -> str:
    if status in {"passed", "expected"}:
        return "none"
    if status == "skipped":
        return "skipped"
    if status in {"timedOut", "timeout"}:
        return "timeout"
    if status == "interrupted":
        return "interrupted"
    joined = " ".join(
        str(error.get("message") if isinstance(error, Mapping) else error)
        for error in errors
    ).lower()
    driver_markers = (
        "browser has been closed",
        "browser disconnected",
        "executable doesn't exist",
        "failed to launch",
        "target page, context or browser has been closed",
        "playwright install",
    )
    if any(marker in joined for marker in driver_markers):
        return "browser_driver"
    return "timeout" if "timeout" in joined else "browser_behavior"


def _job_failure_classification(
    name: str,
    conclusion: object,
    steps: Sequence[Mapping[str, Any]],
) -> str:
    if conclusion in {"success", "skipped", "neutral", None}:
        return "none"
    failed_steps = [
        str(step.get("name") or "").lower()
        for step in steps
        if step.get("conclusion") == "failure"
    ]
    if any("upload" in step or "publish" in step for step in failed_steps):
        return "artifact_publication"
    if "browser" in name.casefold():
        if any(
            marker in step
            for step in failed_steps
            for marker in (
                "install headless chromium",
                "install server and browser dependencies",
                "setup-node",
                "cache",
            )
        ):
            return "browser_driver"
        return "browser_behavior"
    return "job_failure"


def _annotation_metrics(test: Mapping[str, Any]) -> dict[str, Any]:
    annotations = test.get("annotations") or []
    if not isinstance(annotations, Sequence):
        return {}
    for annotation in annotations:
        if not isinstance(annotation, Mapping):
            continue
        if annotation.get("type") != "commander-journey-metrics":
            continue
        description = annotation.get("description")
        if not isinstance(description, str):
            continue
        try:
            parsed = json.loads(description)
        except json.JSONDecodeError:
            continue
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def playwright_metrics(
    document: Mapping[str, Any], *, group: str
) -> list[dict[str, Any]]:
    suites = document.get("suites") or []
    if not isinstance(suites, Sequence):
        raise ValueError("Playwright report must contain a suites list")
    journeys: list[dict[str, Any]] = []
    for spec in _specs(
        suite for suite in suites if isinstance(suite, Mapping)
    ):
        tests = spec.get("tests") or []
        if not isinstance(tests, Sequence):
            raise ValueError("Playwright spec tests must be a list")
        for test in tests:
            if not isinstance(test, Mapping):
                raise ValueError("Every Playwright test must be an object")
            results = test.get("results") or []
            if not isinstance(results, Sequence):
                raise ValueError("Playwright test results must be a list")
            result_rows = [
                result for result in results if isinstance(result, Mapping)
            ]
            final = result_rows[-1] if result_rows else {}
            status = str(final.get("status") or "not_run")
            errors = final.get("errors") or []
            if not isinstance(errors, Sequence):
                errors = []
            journeys.append(
                {
                    "group": group,
                    "title": str(spec.get("title") or "unknown"),
                    "file": spec.get("file"),
                    "status": status,
                    "duration_seconds": round(
                        float(final.get("duration") or 0) / 1000, 3
                    ),
                    "retry_count": max(0, len(result_rows) - 1),
                    "failure_classification": _failure_classification(
                        status, errors
                    ),
                    "game_metrics": _annotation_metrics(test),
                }
            )
    return journeys


def load_browser_reports(directory: Path | None) -> list[tuple[str, dict]]:
    if directory is None or not directory.exists():
        return []
    reports: list[tuple[str, dict]] = []
    for path in sorted(directory.rglob("playwright-*.json")):
        group = path.stem.removeprefix("playwright-")
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"Playwright report {path} must be an object")
        reports.append((group, document))
    return reports


def _owned_report_paths(
    directory: Path,
    *,
    artifact_prefixes: tuple[str, ...],
) -> list[Path]:
    direct_artifact = directory.name.startswith(artifact_prefixes)
    paths = []
    for path in sorted(directory.rglob("*.json")):
        relative = path.relative_to(directory)
        if (
            direct_artifact
            or len(relative.parts) == 1
            or relative.parts[0].startswith(artifact_prefixes)
        ):
            paths.append(path)
    return paths


def load_windows_reports(directory: Path | None) -> list[dict]:
    if directory is None or not directory.exists():
        return []
    reports = []
    for path in _owned_report_paths(
        directory,
        artifact_prefixes=(
            "windows-results-",
            "main-broad-python-windows-",
        ),
    ):
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"Windows report {path} must be an object")
        if document.get("type") not in {
            "unittest-shard-result",
            "pytest-xdist-shard-result",
        }:
            raise ValueError(f"Windows report {path} has an unknown type")
        reports.append(document)
    return reports


def load_python_reports(directory: Path | None) -> list[dict]:
    if directory is None or not directory.exists():
        return []
    reports = []
    for path in _owned_report_paths(
        directory,
        artifact_prefixes=(
            "python-results-",
            "main-broad-python-ubuntu-",
        ),
    ):
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"Python report {path} must be an object")
        if document.get("type") not in {
            "unittest-shard-result",
            "pytest-xdist-shard-result",
        }:
            raise ValueError(f"Python report {path} has an unknown type")
        reports.append(document)
    return reports


def _sum_setup_steps(job: Mapping[str, Any]) -> float | None:
    total = 0.0
    observed = False
    for step in _step_rows(job):
        if step["name"] in {
            "Run focused Windows compatibility suite",
            "Run full Windows shard",
            "Run functional Windows shard",
            "Run sequential generated and governance shard",
        }:
            break
        duration = step["duration_seconds"]
        if duration is not None:
            total += duration
            observed = True
    return round(total, 3) if observed else None


def _step_duration(job: Mapping[str, Any], name: str) -> float | None:
    for step in _step_rows(job):
        if step["name"] == name:
            return step["duration_seconds"]
    return None


def _runner_interval(
    job: Mapping[str, Any],
) -> tuple[datetime | None, datetime | None]:
    steps = job.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        return None, None
    starts = []
    completions = []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        started = _time(step.get("started_at"))
        completed = _time(step.get("completed_at"))
        if started is not None:
            starts.append(started)
        if completed is not None:
            completions.append(completed)
    return (
        min(starts) if starts else None,
        max(completions) if completions else None,
    )


def _maximum_concurrency(jobs: Sequence[Mapping[str, Any]]) -> int | None:
    events = []
    for job in jobs:
        started, completed = _runner_interval(job)
        if started is None or completed is None:
            continue
        events.extend(((started, 1), (completed, -1)))
    if not events:
        return None
    current = 0
    maximum = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        current += delta
        maximum = max(maximum, current)
    return maximum


def windows_metrics(
    run: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]],
    reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    created = _time(str(run.get("created_at") or ""))
    by_name = {str(job.get("name") or "unknown"): job for job in jobs}
    shards = []
    worker_jobs = []
    main_broad = run.get("name") == "Main broad regression"
    for report in reports:
        suite = str(report.get("suite") or "unknown")
        name = (
            f"Main / Broad / Python / windows / {suite}"
            if main_broad
            else (
                "PR / Windows / "
                + ("compatibility" if suite == "windows-compat" else suite)
            )
        )
        job = by_name.get(name)
        if job is not None:
            worker_jobs.append(job)
        started, completed = _runner_interval(job) if job is not None else (None, None)
        shards.append(
            {
                "name": suite,
                "conclusion": job.get("conclusion") if job is not None else None,
                "queue_seconds": (
                    round((started - created).total_seconds(), 3)
                    if started is not None and created is not None
                    else None
                ),
                "setup_duration_seconds": (
                    _sum_setup_steps(job) if job is not None else None
                ),
                "test_duration_seconds": report.get("duration_seconds"),
                "test_count": report.get("tests_run"),
                "skipped_test_count": report.get("skipped"),
                "backend": report.get("backend"),
                "workers": report.get("workers"),
                "distribution": report.get("distribution"),
                "collection_fingerprint": report.get("collection_fingerprint"),
                "module_timings": report.get("module_timings") or [],
                "job_duration_seconds": (
                    round((completed - started).total_seconds(), 3)
                    if started is not None and completed is not None
                    else None
                ),
            }
        )
    package = by_name.get(
        "Main / Broad / Package / windows"
        if main_broad
        else "PR / Windows / package"
    )
    windows_jobs = [
        job
        for job in jobs
        if str(job.get("name") or "").startswith(
            "Main / Broad / Python / windows"
            if main_broad
            else "PR / Windows"
        )
    ]
    completions = [_runner_interval(job)[1] for job in windows_jobs]
    completions = [value for value in completions if value is not None]
    return {
        "shards": sorted(shards, key=lambda row: row["name"]),
        "package_duration_seconds": (
            _step_duration(
                package,
                "Build and verify clean wheel"
                if main_broad
                else "Build and verify Windows wheel",
            )
            if package is not None
            else None
        ),
        "critical_path_seconds_observed": (
            round((max(completions) - created).total_seconds(), 3)
            if completions and created is not None
            else None
        ),
        "max_runner_concurrency_observed": _maximum_concurrency(worker_jobs),
    }


def python_metrics(
    run: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]],
    reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    created = _time(str(run.get("created_at") or ""))
    by_name = {str(job.get("name") or "unknown"): job for job in jobs}
    shards = []
    worker_jobs = []
    main_broad = run.get("name") == "Main broad regression"
    for report in reports:
        suite = str(report.get("suite") or "unknown")
        job = by_name.get(
            f"Main / Broad / Python / ubuntu / {suite}"
            if main_broad
            else f"PR / Python / {suite}"
        )
        if job is not None:
            worker_jobs.append(job)
        started, completed = _runner_interval(job) if job is not None else (None, None)
        shards.append(
            {
                "name": suite,
                "conclusion": job.get("conclusion") if job is not None else None,
                "queue_seconds": (
                    round((started - created).total_seconds(), 3)
                    if started is not None and created is not None
                    else None
                ),
                "test_duration_seconds": report.get("duration_seconds"),
                "test_count": report.get("tests_run"),
                "skipped_test_count": report.get("skipped"),
                "backend": report.get("backend"),
                "workers": report.get("workers"),
                "distribution": report.get("distribution"),
                "collection_fingerprint": report.get("collection_fingerprint"),
                "module_timings": report.get("module_timings") or [],
                "job_duration_seconds": (
                    round((completed - started).total_seconds(), 3)
                    if started is not None and completed is not None
                    else None
                ),
            }
        )
    completions = [_runner_interval(job)[1] for job in worker_jobs]
    completions = [value for value in completions if value is not None]
    return {
        "shards": sorted(shards, key=lambda row: row["name"]),
        "critical_path_seconds_observed": (
            round((max(completions) - created).total_seconds(), 3)
            if completions and created is not None
            else None
        ),
        "max_job_concurrency_observed": _maximum_concurrency(worker_jobs),
    }


def build_metrics(
    run: Mapping,
    jobs_document: Mapping,
    browser_reports: Sequence[tuple[str, Mapping[str, Any]]] = (),
    windows_reports: Sequence[Mapping[str, Any]] = (),
    python_reports: Sequence[Mapping[str, Any]] = (),
) -> dict:
    jobs = jobs_document.get("jobs")
    if not isinstance(jobs, Sequence):
        raise ValueError("Jobs document must contain a jobs list")
    rows = []
    for job in jobs:
        if not isinstance(job, Mapping):
            raise ValueError("Every jobs entry must be an object")
        step_rows = _step_rows(job)
        name = str(job.get("name") or "unknown")
        rows.append(
            {
                "name": name,
                "conclusion": job.get("conclusion"),
                "failure_classification": _job_failure_classification(
                    name,
                    job.get("conclusion"),
                    step_rows,
                ),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "duration_seconds": _duration(
                    job.get("started_at"), job.get("completed_at")
                ),
                "steps": step_rows,
            }
        )
    created = _time(str(run.get("created_at") or ""))
    starts = [_time(row["started_at"]) for row in rows]
    starts = [value for value in starts if value is not None]
    completions = [_time(row["completed_at"]) for row in rows]
    completions = [value for value in completions if value is not None]
    queue = (
        round((min(starts) - created).total_seconds(), 3)
        if created is not None and starts
        else None
    )
    critical = (
        round((max(completions) - created).total_seconds(), 3)
        if created is not None and completions
        else None
    )
    journeys = [
        journey
        for group, document in browser_reports
        for journey in playwright_metrics(document, group=group)
    ]
    return {
        "schema_version": 5,
        "run_id": run.get("id"),
        "head_sha": run.get("head_sha"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "queue_seconds": queue,
        "critical_path_seconds_observed": critical,
        "cache_hit_rate": None,
        "agent_idle_seconds": None,
        "stale_run_cancellation_count": None,
        "jobs": sorted(rows, key=lambda row: row["name"]),
        "browser_journeys": sorted(
            journeys, key=lambda row: (row["group"], row["title"])
        ),
        "windows": windows_metrics(run, jobs, windows_reports),
        "python": python_metrics(run, jobs, python_reports),
    }


def markdown(metrics: Mapping) -> str:
    lines = [
        "## CI duration report",
        "",
        f"- Queue: {metrics['queue_seconds']} seconds",
        f"- Observed critical path: {metrics['critical_path_seconds_observed']} seconds",
        "- Cache-hit rate: unavailable from the jobs API (not estimated)",
        "- Agent idle time: unavailable from GitHub Actions (not estimated)",
        "- Stale-run cancellation count: unavailable per run (not estimated)",
        "",
        "| Job | Conclusion | Failure class | Duration (seconds) |",
        "|---|---|---|---:|",
    ]
    for row in metrics["jobs"]:
        lines.append(
            f"| {row['name']} | {row['conclusion']} | "
            f"{row['failure_classification']} | {row['duration_seconds']} |"
        )
    journeys = metrics.get("browser_journeys") or []
    if journeys:
        lines.extend(
            [
                "",
                "| Browser group | Journey | Status | Duration | Retries | Failure class |",
                "|---|---|---|---:|---:|---|",
            ]
        )
        for journey in journeys:
            lines.append(
                f"| {journey['group']} | {journey['title']} | "
                f"{journey['status']} | {journey['duration_seconds']} | "
                f"{journey['retry_count']} | "
                f"{journey['failure_classification']} |"
            )
    windows = metrics.get("windows") or {}
    windows_shards = windows.get("shards") or []
    if windows_shards:
        lines.extend(
            [
                "",
                "- Windows critical path: "
                f"{windows.get('critical_path_seconds_observed')} seconds",
                f"- Windows package: {windows.get('package_duration_seconds')} seconds",
                "- Observed Windows runner concurrency: "
                f"{windows.get('max_runner_concurrency_observed')}",
                "",
                "| Windows shard | Queue | Setup | Tests | Test duration | Job duration |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for shard in windows_shards:
            lines.append(
                f"| {shard['name']} | {shard['queue_seconds']} | "
                f"{shard['setup_duration_seconds']} | {shard['test_count']} | "
                f"{shard['test_duration_seconds']} | {shard['job_duration_seconds']} |"
            )
    python = metrics.get("python") or {}
    python_shards = python.get("shards") or []
    if python_shards:
        lines.extend(
            [
                "",
                "- Python critical path: "
                f"{python.get('critical_path_seconds_observed')} seconds",
                "- Observed Python job concurrency: "
                f"{python.get('max_job_concurrency_observed')}",
                "",
                "| Python shard | Backend | Workers | Tests | Test duration | Job duration |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for shard in python_shards:
            lines.append(
                f"| {shard['name']} | {shard['backend']} | {shard['workers']} | "
                f"{shard['test_count']} | {shard['test_duration_seconds']} | "
                f"{shard['job_duration_seconds']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize GitHub Actions timing")
    parser.add_argument("--run-json", required=True)
    parser.add_argument("--jobs-json", required=True)
    parser.add_argument("--browser-report-dir")
    parser.add_argument("--windows-report-dir")
    parser.add_argument("--python-report-dir")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary")
    args = parser.parse_args()
    run = json.loads(Path(args.run_json).read_text(encoding="utf-8"))
    jobs = json.loads(Path(args.jobs_json).read_text(encoding="utf-8"))
    reports = load_browser_reports(
        Path(args.browser_report_dir) if args.browser_report_dir else None
    )
    windows_reports = load_windows_reports(
        Path(args.windows_report_dir) if args.windows_report_dir else None
    )
    python_reports = load_python_reports(
        Path(args.python_report_dir) if args.python_report_dir else None
    )
    metrics = build_metrics(
        run,
        jobs,
        reports,
        windows_reports,
        python_reports,
    )
    Path(args.output).write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if args.summary:
        with Path(args.summary).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(markdown(metrics))
    print(json.dumps(metrics, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
