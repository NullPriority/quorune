from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib

try:
    from scripts.certification_receipt import RECEIPT_SCHEMA_VERSION
    from scripts.source_tree_fingerprint import (
        SOURCE_TREE_FINGERPRINT_ALGORITHM,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from certification_receipt import RECEIPT_SCHEMA_VERSION  # type: ignore[no-redef]
    from source_tree_fingerprint import (  # type: ignore[no-redef]
        SOURCE_TREE_FINGERPRINT_ALGORITHM,
    )


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "platform" / "readiness-source.json"
JSON_OUTPUT = ROOT / "coverage" / "platform-readiness.json"
MARKDOWN_OUTPUT = ROOT / "coverage" / "platform-readiness.md"
STATUS_OUTPUT = ROOT / "docs" / "PLATFORM_IMPLEMENTATION_STATUS.md"
TEST_SHARDS = ROOT / "platform" / "test-shards.json"
PLATFORM_INPUT_FINGERPRINT_ALGORITHM = (
    "platform-readiness-derived-inputs-sha256-v1"
)


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _validate_provenance(source: dict) -> None:
    provenance = source.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != {
        "certification_policy",
        "certification_receipt_schema_version",
        "source_tree_fingerprint_algorithm",
    }:
        raise ValueError("platform readiness source requires provenance")
    if (
        provenance["source_tree_fingerprint_algorithm"]
        != SOURCE_TREE_FINGERPRINT_ALGORITHM
    ):
        raise ValueError("platform readiness fingerprint algorithm is unsupported")
    if provenance["certification_receipt_schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise ValueError("platform readiness receipt schema version is unsupported")
    if provenance["certification_policy"] != (
        "exact_head_actions_receipt_and_source_tree_equivalence"
    ):
        raise ValueError("platform readiness certification policy is unsupported")
    if "integration" in source:
        raise ValueError(
            "transient integration chronology is not durable readiness state"
        )
    if "next_task" in source:
        raise ValueError(
            "next-task selection belongs to the generated rules scheduler"
        )
    if "card_program_census" in source.get("validation", {}):
        raise ValueError(
            "card_program_census must be derived from authoritative coverage artifacts"
        )
    ci = source.get("validation", {}).get("ci")
    if not isinstance(ci, dict) or set(ci) != {"matrix", "policy"}:
        raise ValueError("durable CI readiness policy is incomplete")
    serialized = json.dumps(source, sort_keys=True).lower()
    forbidden = (
        "active_candidate",
        "active_phase",
        "feature_head_sha",
        "certified_head_sha",
        "generation_timestamp",
        "pull_requests",
        "run_id",
        "runtime_branch",
    )
    leaked = [value for value in forbidden if value in serialized]
    if leaked:
        raise ValueError(
            "ephemeral execution state leaked into durable readiness: "
            + ", ".join(leaked)
        )


def _project_metadata() -> dict:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return project["project"]


def _test_inventory() -> dict:
    manifest = _load_json(TEST_SHARDS)
    primary = manifest.get("primary_shards")
    overlays = manifest.get("overlay_suites")
    if not isinstance(primary, dict) or not isinstance(overlays, dict):
        raise ValueError("platform/test-shards.json has invalid suite maps")
    modules = [
        str(module)
        for suite_modules in primary.values()
        for module in suite_modules
    ]
    if len(modules) != len(set(modules)):
        raise ValueError("primary test-shard modules must be uniquely owned")
    generated = primary.get("generated-validation")
    if not isinstance(generated, list):
        raise ValueError("generated-validation must be a primary test shard")
    return {
        "primary_test_modules": len(modules),
        "primary_test_shards": len(primary),
        "generated_validation_modules": len(generated),
        "overlay_test_suites": len(overlays),
    }


def _input_fingerprint(value: dict) -> str:
    payload = {
        "algorithm": PLATFORM_INPUT_FINGERPRINT_ALGORITHM,
        "inputs": value,
    }
    serialized = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _file_count(relative: str) -> int:
    """Count the paths included in the Git source checkpoint."""
    tracked = subprocess.check_output(
        ["git", "ls-files", "--cached", "--deduplicate", "-z", "--", f"{relative}/"],
        cwd=ROOT,
    )
    return tracked.count(b"\0")


def _optional_json(relative: str) -> dict | None:
    path = ROOT / relative
    return _load_json(path) if path.is_file() else None


def _rules_metrics() -> dict:
    manifest = _optional_json("rules/manifest.json")
    conformance = _optional_json("coverage/rules-conformance.json")
    mechanics = _optional_json("coverage/mechanics-coverage.json")
    oracle = _optional_json("coverage/oracle-coverage.json")
    commander = _optional_json("coverage/oracle-coverage-commander.json")
    return {
        "manifest_present": manifest is not None,
        "effective_date": (manifest or {}).get("effective_date"),
        "source_sha256": (manifest or {}).get("source_sha256"),
        "rules": {
            "total": (conformance or {}).get("total_cases"),
            "passing": (conformance or {}).get("semantic_passing_cases"),
            "blocked": (conformance or {}).get("blocked_cases"),
            "definition_only": (conformance or {}).get("definition_only_cases"),
            "unreviewed": (conformance or {}).get("unreviewed_cases"),
        },
        "mechanics": {
            "total": (mechanics or {}).get("total_mechanics"),
            "trusted": (mechanics or {}).get("trusted_mechanics"),
            "status_counts": (mechanics or {}).get("status_counts"),
        },
        "oracle": {
            "total": (oracle or {}).get("total_oracle_ids"),
            "status_counts": (oracle or {}).get("status_counts"),
            "material_residuals": (oracle or {}).get("material_residuals"),
        },
        "commander_oracle": {
            "total": (commander or {}).get("total_oracle_ids"),
            "status_counts": (commander or {}).get("status_counts"),
            "material_residuals": (commander or {}).get("material_residuals"),
        },
        "current_snapshot_complete": bool(
            (conformance or {}).get("current_snapshot_complete")
            and (mechanics or {}).get("current_snapshot_complete")
            and (oracle or {}).get("current_snapshot_complete")
            and (commander or {}).get("current_snapshot_complete")
        ),
    }


def _card_program_metrics() -> dict:
    def summary(relative: str) -> dict:
        value = _optional_json(relative) or {}
        return {
            "cards_considered": value.get("cards_considered"),
            "status_counts": value.get("status_counts"),
            "trust_basis_counts": value.get("trust_basis_counts"),
            "material_residuals": value.get("material_residuals"),
        }

    return {
        "full": summary("coverage/card-program-coverage.json"),
        "commander": summary(
            "coverage/card-program-coverage-commander.json"
        ),
    }


def build_report() -> dict:
    source = _load_json(SOURCE)
    if source.get("schema_version") != 3:
        raise ValueError("Unsupported platform readiness source schema")
    _validate_provenance(source)
    project = _project_metadata()
    package = {
        "name": str(project["name"]),
        "version": str(project["version"]),
        "python": str(project["requires-python"]),
    }
    tests = {
        **_test_inventory(),
        "schema_files": _file_count("schemas")
        + _file_count("quorune/schemas"),
        "server_files": _file_count("server"),
        "web_files": _file_count("web"),
        "migration_files": _file_count("migrations"),
    }
    rules_coverage = _rules_metrics()
    card_program_census = _card_program_metrics()
    input_fingerprint = _input_fingerprint(
        {
            "source": source,
            "package": package,
            "tests": tests,
            "rules_coverage": rules_coverage,
            "card_program_census": card_program_census,
        }
    )
    report = copy.deepcopy(source)
    report["generated"] = {
        "generator": "scripts/update_platform_status.py",
        "source": "platform/readiness-source.json",
        "stale_check": "python scripts/update_platform_status.py --check",
        "evaluated_input_fingerprint": input_fingerprint,
        "input_fingerprint_algorithm": (
            PLATFORM_INPUT_FINGERPRINT_ALGORITHM
        ),
    }
    report["package"] = package
    report["tests"] = tests
    report["rules_coverage"] = rules_coverage
    report["validation"]["card_program_census"] = card_program_census
    return report


def _value(value: object) -> str:
    if value is None:
        return "not available"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        return ", ".join(f"{key}={item}" for key, item in sorted(value.items()))
    return str(value)


def render_readiness(report: dict) -> str:
    generated = report["generated"]
    fingerprint = hashlib.sha256(_serialize_json(report).encode("utf-8")).hexdigest()
    command = r".\.venv\Scripts\python.exe scripts\update_platform_status.py --write"
    lines = [
        "---",
        'title: "Platform readiness"',
        'status: "generated"',
        'authoritative_source: "platform/readiness-source.json"',
        f'verified: "{fingerprint}"',
        'audience: "maintainers, operators, and contributors"',
        'maintenance: "generated"',
        'generated_source: "coverage/platform-readiness.json"',
        f'generation_command: "{command}"',
        "---",
        "",
        "# Platform readiness",
        "",
        f"Source fingerprint: `{fingerprint}`",
        "",
        "## Current top-level state",
        "",
        f"- Package: `{report['package']['version']}`",
        f"- Authoritative kernel: `{report['platform']['authoritative_kernel']}`",
        f"- Server runtime: `{report['platform']['http_websocket_server']}`",
        f"- Browser client: `{report['platform']['browser_client']}`",
        f"- Durable persistence: `{report['platform']['durable_database']}`",
        f"- Exact command replay: `{report['platform']['replay']}`",
        f"- Hidden-information projection: `{report['platform']['hidden_information']}`",
        f"- Core AI dependency: `{report['platform']['ai_dependency']}`",
        f"- Primary test modules: `{report['tests']['primary_test_modules']}`",
        f"- Primary test shards: `{report['tests']['primary_test_shards']}`",
        f"- Rules snapshot integrated: {_value(report['rules_coverage']['manifest_present'])}",
        f"- Rules snapshot complete: {_value(report['rules_coverage']['current_snapshot_complete'])}",
    ]
    lines.extend(
        [
            "",
            "## Top blockers",
            "",
            *(f"- {blocker}" for blocker in report["blockers"][:5]),
            "",
            "Complete inventories and provenance are in the "
            "[machine-readable platform report](platform-readiness.json).",
            "",
            "Exact generation command:",
            "",
            "```powershell",
            command,
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_status(report: dict) -> str:
    generated = report["generated"]
    rules = report["rules_coverage"]
    fingerprint = hashlib.sha256(_serialize_json(report).encode("utf-8")).hexdigest()
    command = r".\.venv\Scripts\python.exe scripts\update_platform_status.py --write"
    lines = [
        "---",
        'title: "Platform implementation status"',
        'status: "generated"',
        'authoritative_source: "platform/readiness-source.json"',
        f'verified: "{fingerprint}"',
        'audience: "maintainers, operators, and contributors"',
        'maintenance: "generated"',
        'generated_source: "coverage/platform-readiness.json"',
        f'generation_command: "{command}"',
        "---",
        "",
        "# Platform implementation status",
        "",
        f"Source fingerprint: `{fingerprint}`",
        "",
        "## Current top-level state",
        "",
        f"- Package version: `{report['package']['version']}`",
        f"- Authoritative kernel: `{report['platform']['authoritative_kernel']}`",
        f"- Server runtime: `{report['platform']['http_websocket_server']}`",
        f"- Browser client: `{report['platform']['browser_client']}`",
        f"- Durable persistence: `{report['platform']['durable_database']}`",
        f"- Exact replay: `{report['platform']['replay']}`",
        f"- Hidden-information projection: `{report['platform']['hidden_information']}`",
        f"- Core AI dependency: `{report['platform']['ai_dependency']}`",
        f"- Primary test modules: `{report['tests']['primary_test_modules']}`",
        f"- Primary test shards: `{report['tests']['primary_test_shards']}`",
        f"- Rules snapshot integrated: {_value(rules['manifest_present'])}",
        f"- Rules snapshot complete: {_value(rules['current_snapshot_complete'])}",
            "",
            "## Top blockers",
            "",
            *(f"- {blocker}" for blocker in report["blockers"][:5]),
            "",
            "Complete platform, validation, milestone, and provenance data is in the "
            "[machine-readable platform report](../coverage/platform-readiness.json).",
            "",
            "Exact generation command:",
            "",
            "```powershell",
            command,
            "```",
            "",
    ]
    return "\n".join(lines)


def _serialize_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def _outputs(report: dict) -> dict[Path, str]:
    return {
        JSON_OUTPUT: _serialize_json(report),
        MARKDOWN_OUTPUT: render_readiness(report),
        STATUS_OUTPUT: render_status(report),
    }


def write_outputs(report: dict) -> None:
    for path, content in _outputs(report).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def check_outputs(report: dict) -> list[str]:
    stale: list[str] = []
    for path, expected in _outputs(report).items():
        actual = path.read_text(encoding="utf-8") if path.is_file() else None
        if actual != expected:
            stale.append(path.relative_to(ROOT).as_posix())
    return stale


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = build_report()
    if args.write:
        write_outputs(report)
        print(
            json.dumps(
                {
                    "ok": True,
                    "outputs": [
                        path.relative_to(ROOT).as_posix() for path in _outputs(report)
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    stale = check_outputs(report)
    if stale:
        print(
            "platform status is stale; run "
            "`python scripts/update_platform_status.py --write`: "
            + ", ".join(stale),
            file=sys.stderr,
        )
        if JSON_OUTPUT.relative_to(ROOT).as_posix() in stale:
            actual = (
                JSON_OUTPUT.read_text(encoding="utf-8").splitlines()
                if JSON_OUTPUT.is_file()
                else []
            )
            expected = _serialize_json(report).splitlines()
            diagnostic = list(
                difflib.unified_diff(
                    actual,
                    expected,
                    fromfile="tracked platform status",
                    tofile="expected platform status",
                    lineterm="",
                )
            )
            print("\n".join(diagnostic[:80]), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "stale_outputs": []}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
