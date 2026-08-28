from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.shard_result_validation import (
    ShardResultError,
    result_documents,
    validate_result_document,
)
from scripts.test_shards import load_manifest, validate_partition


class WindowsCertificationError(ValueError):
    pass


def expected_suites(*, full: bool) -> tuple[str, ...]:
    manifest = load_manifest()
    validate_partition(manifest)
    if full:
        return tuple(manifest["execution_order"])
    return ("windows-compat",)


def validate_dependencies(needs: Mapping, *, full: bool) -> None:
    expected = {
        "plan": "success",
        "windows_compatibility": "skipped" if full else "success",
        "windows_full": "success" if full else "skipped",
        "windows_package": "success" if full else "skipped",
    }
    failures = {}
    for name, result in expected.items():
        details = needs.get(name)
        actual = details.get("result") if isinstance(details, Mapping) else None
        if actual != result:
            failures[name] = {"expected": result, "actual": actual}
    unexpected = sorted(set(needs) - set(expected))
    if failures or unexpected:
        raise WindowsCertificationError(
            json.dumps(
                {"dependency_failures": failures, "unexpected": unexpected},
                sort_keys=True,
            )
        )


def validate_results(directory: Path, *, full: bool) -> dict:
    expected = expected_suites(full=full)
    documents = result_documents(directory)
    observed: dict[str, dict] = {}
    for document in documents:
        suite = document.get("suite")
        if not isinstance(suite, str) or suite in observed:
            raise WindowsCertificationError("Windows shard result suite is invalid or duplicated")
        backend = (
            "unittest"
            if full and suite == "generated-validation"
            else "pytest-xdist"
        )
        try:
            observed[suite] = validate_result_document(
                document,
                expected_suite=suite,
                expected_platform="windows",
                expected_backend=backend,
            )
        except ShardResultError as exc:
            raise WindowsCertificationError(str(exc)) from exc
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    if missing or extra:
        raise WindowsCertificationError(
            json.dumps({"missing_results": missing, "extra_results": extra}, sort_keys=True)
        )
    return {
        "mode": "full" if full else "focused",
        "suites": len(observed),
        "tests_run": sum(document["tests_run"] for document in observed.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Windows CI certification")
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--full", choices=("true", "false"), required=True)
    args = parser.parse_args()
    raw = os.environ.get("CI_WINDOWS_NEEDS_JSON")
    if not raw:
        print("CI_WINDOWS_NEEDS_JSON is required")
        return 1
    try:
        needs = json.loads(raw)
        if not isinstance(needs, dict):
            raise WindowsCertificationError("CI_WINDOWS_NEEDS_JSON must contain an object")
        full = args.full == "true"
        validate_dependencies(needs, full=full)
        summary = validate_results(Path(args.results_dir), full=full)
    except (
        json.JSONDecodeError,
        OSError,
        ShardResultError,
        WindowsCertificationError,
        ValueError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
