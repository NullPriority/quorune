from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "platform" / "ci-escape-source.json"
JSON_OUTPUT = ROOT / "coverage" / "ci-escape-report.json"
MARKDOWN_OUTPUT = ROOT / "coverage" / "ci-escape-report.md"

_CATEGORIES = {
    "source_correctness",
    "missing_affected_test",
    "generated_artifact_drift",
    "architecture_or_documentation",
    "package_or_install",
    "browser_integration",
    "windows_compatibility",
    "flaky_test",
    "infrastructure",
}
_EDGE_STATUSES = {"added", "not_applicable", "pending"}


def _strings(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a list of nonempty strings")
    return list(value)


def _validate_source(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "observed_at",
        "measurement_window",
        "repository",
        "escapes",
        "recent_pull_requests",
        "known_flaky_tests",
        "limitations",
    }:
        raise ValueError("CI escape source has unknown or missing fields")
    if value["schema_version"] != 2:
        raise ValueError("Unsupported CI escape source schema")
    for field in ("observed_at", "repository"):
        if not isinstance(value[field], str) or not value[field]:
            raise ValueError(f"{field} must be nonempty")
    measurement_window = value["measurement_window"]
    if not isinstance(measurement_window, dict) or set(measurement_window) != {
        "started_at",
        "ended_at",
        "population",
        "sample_size",
    }:
        raise ValueError("measurement_window has unknown or missing fields")
    for field in ("started_at", "ended_at", "population"):
        if (
            not isinstance(measurement_window[field], str)
            or not measurement_window[field]
        ):
            raise ValueError(f"measurement_window.{field} must be nonempty")
    if (
        type(measurement_window["sample_size"]) is not int
        or measurement_window["sample_size"] < 0
    ):
        raise ValueError("measurement_window.sample_size must be nonnegative")
    escapes = value["escapes"]
    if not isinstance(escapes, list):
        raise ValueError("escapes must be a list")
    escape_fields = {
        "id",
        "run_id",
        "head_sha",
        "category",
        "deterministic",
        "summary",
        "failed_surfaces",
        "regressions",
        "impact_edge_status",
        "resolution",
    }
    seen: set[str] = set()
    for index, escape in enumerate(escapes):
        if not isinstance(escape, dict) or set(escape) != escape_fields:
            raise ValueError(f"escapes[{index}] has unknown or missing fields")
        escape_id = escape["id"]
        if not isinstance(escape_id, str) or not escape_id or escape_id in seen:
            raise ValueError(f"escapes[{index}].id must be unique and nonempty")
        seen.add(escape_id)
        if type(escape["run_id"]) is not int or escape["run_id"] <= 0:
            raise ValueError(f"escapes[{index}].run_id must be positive")
        if not isinstance(escape["head_sha"], str) or len(escape["head_sha"]) != 40:
            raise ValueError(f"escapes[{index}].head_sha must be a full SHA")
        if escape["category"] not in _CATEGORIES:
            raise ValueError(f"escapes[{index}].category is unsupported")
        if type(escape["deterministic"]) is not bool:
            raise ValueError(f"escapes[{index}].deterministic must be boolean")
        for field in ("summary", "resolution"):
            if not isinstance(escape[field], str) or not escape[field]:
                raise ValueError(f"escapes[{index}].{field} must be nonempty")
        _strings(escape["failed_surfaces"], field=f"escapes[{index}].failed_surfaces")
        _strings(escape["regressions"], field=f"escapes[{index}].regressions")
        if escape["impact_edge_status"] not in _EDGE_STATUSES:
            raise ValueError(f"escapes[{index}].impact_edge_status is unsupported")
    pull_requests = value["recent_pull_requests"]
    if not isinstance(pull_requests, list):
        raise ValueError("recent_pull_requests must be a list")
    pr_fields = {
        "number",
        "head_sha",
        "final_run_id",
        "final_run_conclusion",
        "workflow_runs_observed",
        "push_count",
        "critical_path_seconds",
        "slot_b_inactive_seconds",
    }
    for index, row in enumerate(pull_requests):
        if not isinstance(row, dict) or set(row) != pr_fields:
            raise ValueError(
                f"recent_pull_requests[{index}] has unknown or missing fields"
            )
        for field in ("number", "final_run_id", "workflow_runs_observed"):
            if type(row[field]) is not int or row[field] <= 0:
                raise ValueError(f"recent_pull_requests[{index}].{field} must be positive")
        if not isinstance(row["head_sha"], str) or len(row["head_sha"]) != 40:
            raise ValueError(
                f"recent_pull_requests[{index}].head_sha must be a full SHA"
            )
        if row["final_run_conclusion"] not in {
            "success",
            "failure",
            "cancelled",
        }:
            raise ValueError(
                f"recent_pull_requests[{index}].final_run_conclusion is invalid"
            )
        for field in (
            "push_count",
            "critical_path_seconds",
            "slot_b_inactive_seconds",
        ):
            if row[field] is not None and (
                not isinstance(row[field], (int, float)) or row[field] < 0
            ):
                raise ValueError(
                    f"recent_pull_requests[{index}].{field} must be nonnegative or null"
                )
    if measurement_window["sample_size"] != len(pull_requests):
        raise ValueError(
            "measurement_window.sample_size must match recent_pull_requests"
        )
    _strings(value["known_flaky_tests"], field="known_flaky_tests")
    limitations = value["limitations"]
    if not isinstance(limitations, dict) or set(limitations) != {
        "active_development_hours",
        "average_pushes_per_merged_pr",
        "first_eligible_head_certification",
        "slot_b_inactive_seconds",
    } or not all(isinstance(item, str) and item for item in limitations.values()):
        raise ValueError("limitations must explain every unavailable aggregate")
    return value


def build_report(source: Mapping) -> dict:
    value = _validate_source(dict(source))
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    escapes = value["escapes"]
    categories = Counter(escape["category"] for escape in escapes)
    deterministic = [escape for escape in escapes if escape["deterministic"]]
    pending = [
        escape["id"]
        for escape in escapes
        if escape["impact_edge_status"] == "pending"
    ]
    pull_requests = value["recent_pull_requests"]
    push_counts = [row["push_count"] for row in pull_requests if row["push_count"] is not None]
    critical_paths = [
        float(row["critical_path_seconds"])
        for row in pull_requests
        if row["critical_path_seconds"] is not None
    ]
    inactive = [
        float(row["slot_b_inactive_seconds"])
        for row in pull_requests
        if row["slot_b_inactive_seconds"] is not None
    ]
    exact_head_passes = sum(
        row["final_run_conclusion"] == "success" for row in pull_requests
    )
    return {
        "schema_version": 2,
        "repository": value["repository"],
        "observed_at": value["observed_at"],
        "measurement_window": value["measurement_window"],
        "source_fingerprint": hashlib.sha256(canonical).hexdigest(),
        "summary": {
            "escape_count": len(escapes),
            "deterministic_escape_count": len(deterministic),
            "category_counts": dict(sorted(categories.items())),
            "repeated_failure_categories": sorted(
                category for category, count in categories.items() if count > 1
            ),
            "current_missing_impact_edges": pending,
            "known_flaky_test_count": len(value["known_flaky_tests"]),
            "average_pushes_per_merged_pr": (
                round(mean(push_counts), 3)
                if len(push_counts) == len(pull_requests) and push_counts
                else None
            ),
            "first_eligible_head_certification_pass_rate": None,
            "first_eligible_head_certification_sample_size": 0,
            "eventual_final_head_certification_pass_rate": (
                round(exact_head_passes / len(pull_requests), 6)
                if pull_requests
                else None
            ),
            "eventual_final_head_certification_sample_size": len(pull_requests),
            "average_critical_path_seconds": (
                round(mean(critical_paths), 3) if critical_paths else None
            ),
            "average_slot_b_inactive_seconds": (
                round(mean(inactive), 3)
                if len(inactive) == len(pull_requests) and inactive
                else None
            ),
        },
        "escapes": escapes,
        "recent_pull_requests": pull_requests,
        "known_flaky_tests": value["known_flaky_tests"],
        "limitations": value["limitations"],
    }


def _measurement(value: object) -> str:
    return "null" if value is None else str(value)


def markdown(report: Mapping) -> str:
    summary = report["summary"]
    lines = [
        "---",
        'title: "CI escape report"',
        'status: "generated"',
        'authoritative_source: "platform/ci-escape-source.json"',
        f'verified: "{report["source_fingerprint"]}"',
        'audience: "maintainers and contributors"',
        'maintenance: "generated"',
        "---",
        "",
        "# CI escape report",
        "",
        "This report classifies observed deterministic failures that escaped the local quick gate. Null measurements are unavailable and are never estimated.",
        "",
        "## Summary",
        "",
        f'- Observation window: {report["measurement_window"]["started_at"]} through {report["measurement_window"]["ended_at"]}',
        f'- Observed population: {report["measurement_window"]["population"]} (n={report["measurement_window"]["sample_size"]})',
        f'- Escapes: {summary["escape_count"]}',
        f'- Deterministic escapes: {summary["deterministic_escape_count"]}',
        f'- Current missing impact edges: {len(summary["current_missing_impact_edges"])}',
        f'- Known flaky tests: {summary["known_flaky_test_count"]}',
        "- Average pushes per merged PR: "
        f'{_measurement(summary["average_pushes_per_merged_pr"])}',
        "- First eligible-head certification pass rate: "
        f'{_measurement(summary["first_eligible_head_certification_pass_rate"])} '
        f'(n={summary["first_eligible_head_certification_sample_size"]})',
        "- Eventual final-head certification pass rate: "
        f'{_measurement(summary["eventual_final_head_certification_pass_rate"])} '
        f'(n={summary["eventual_final_head_certification_sample_size"]})',
        f'- Average observed critical path: {summary["average_critical_path_seconds"]} seconds',
        "- Average Slot B inactive time: "
        f'{_measurement(summary["average_slot_b_inactive_seconds"])}',
        "",
        "## Escapes",
        "",
        "| ID | Run | Category | Impact edge | Resolution |",
        "|---|---:|---|---|---|",
    ]
    for escape in report["escapes"]:
        lines.append(
            f'| `{escape["id"]}` | [{escape["run_id"]}](https://github.com/{report["repository"]}/actions/runs/{escape["run_id"]}) | `{escape["category"]}` | `{escape["impact_edge_status"]}` | {escape["resolution"]} |'
        )
    lines.extend(
        [
            "",
            "## Measurement limitations",
            "",
            f'- First eligible-head certification: {report["limitations"]["first_eligible_head_certification"]}',
            f'- Active development hours: {report["limitations"]["active_development_hours"]}',
            f'- Average pushes per merged PR: {report["limitations"]["average_pushes_per_merged_pr"]}',
            f'- Slot B inactive time: {report["limitations"]["slot_b_inactive_seconds"]}',
            "",
        ]
    )
    return "\n".join(lines)


def _render() -> tuple[str, str]:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    report = build_report(source)
    return (
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        markdown(report),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Update generated CI escape reports")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    json_text, markdown_text = _render()
    if args.write:
        JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        JSON_OUTPUT.write_text(json_text, encoding="utf-8", newline="\n")
        MARKDOWN_OUTPUT.write_text(markdown_text, encoding="utf-8", newline="\n")
        return 0
    stale = []
    for path, expected in (
        (JSON_OUTPUT, json_text),
        (MARKDOWN_OUTPUT, markdown_text),
    ):
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            stale.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    if stale:
        print(json.dumps({"stale": stale}, sort_keys=True))
        return 1
    print(json.dumps({"stale": []}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
