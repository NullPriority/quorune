from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from scripts.test_shards import (
    PYTEST_XDIST_DISTRIBUTION,
    PYTEST_XDIST_WORKERS,
    TEST_COLLECTION_FINGERPRINT_ALGORITHM,
    canonical_test_ids,
    load_manifest,
    load_suite,
    suite_modules,
    test_collection_fingerprint,
)


class ShardResultError(ValueError):
    pass


RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "type",
        "platform",
        "suite",
        "modules",
        "configured_test_count",
        "tests_run",
        "duration_seconds",
        "successful",
        "failures",
        "errors",
        "skipped",
        "expected_failures",
        "unexpected_successes",
        "failed_test_ids",
        "error_test_ids",
        "backend",
        "workers",
        "distribution",
        "collection_fingerprint_algorithm",
        "collection_fingerprint",
        "collection_parity",
        "module_timings",
    }
)


def result_documents(directory: Path) -> list[dict]:
    documents: list[dict] = []
    for path in sorted(directory.rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ShardResultError(f"{path} must contain an object")
        documents.append(value)
    return documents


@lru_cache(maxsize=None)
def suite_expectation(suite: str) -> tuple[tuple[str, ...], int, str]:
    modules = suite_modules(load_manifest(), suite)
    identifiers = canonical_test_ids(load_suite(modules))
    return modules, len(identifiers), test_collection_fingerprint(identifiers)


def _exact_nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ShardResultError(f"{field} must be an exact nonnegative integer")
    return value


def _nonnegative_number(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or not math.isfinite(value)
    ):
        raise ShardResultError(f"{field} must be a nonnegative number")
    return float(value)


def validate_result_document(
    document: Mapping,
    *,
    expected_suite: str,
    expected_platform: str,
    expected_backend: str,
    require_success: bool = True,
) -> dict:
    if set(document) != RESULT_FIELDS:
        raise ShardResultError(
            f"Shard result has invalid fields: "
            f"missing={sorted(RESULT_FIELDS - set(document))} "
            f"unknown={sorted(set(document) - RESULT_FIELDS)}"
        )
    if document["schema_version"] != 3:
        raise ShardResultError("Shard result has an unsupported schema")
    if document["suite"] != expected_suite:
        raise ShardResultError("Shard result suite does not match its assignment")
    if document["platform"] != expected_platform:
        raise ShardResultError("Shard result platform does not match its runner")
    modules, expected_count, expected_fingerprint = suite_expectation(expected_suite)
    raw_modules = document["modules"]
    if (
        not isinstance(raw_modules, Sequence)
        or isinstance(raw_modules, (str, bytes))
        or tuple(raw_modules) != modules
    ):
        raise ShardResultError(
            f"Shard {expected_suite!r} modules do not match the manifest"
        )
    configured = _exact_nonnegative_int(
        document["configured_test_count"], field="configured_test_count"
    )
    tests_run = _exact_nonnegative_int(document["tests_run"], field="tests_run")
    if configured <= 0 or tests_run <= 0:
        raise ShardResultError(f"Shard {expected_suite!r} ran zero tests")
    if configured != expected_count or (
        tests_run != expected_count
        if require_success
        else tests_run > expected_count
    ):
        raise ShardResultError(
            f"Shard {expected_suite!r} did not execute its exact collection"
        )
    _nonnegative_number(document["duration_seconds"], field="duration_seconds")
    for field in (
        "failures",
        "errors",
        "skipped",
        "expected_failures",
        "unexpected_successes",
    ):
        _exact_nonnegative_int(document[field], field=field)
    for count_field, ids_field in (
        ("failures", "failed_test_ids"),
        ("errors", "error_test_ids"),
    ):
        identifiers = document[ids_field]
        if (
            not isinstance(identifiers, list)
            or any(not isinstance(item, str) or not item for item in identifiers)
            or identifiers != sorted(set(identifiers))
            or len(identifiers) != document[count_field]
        ):
            raise ShardResultError(
                f"{ids_field} must exactly identify every reported outcome"
            )
    expected_identifiers = set(canonical_test_ids(load_suite(modules)))
    reported_identifiers = set(document["failed_test_ids"]) | set(
        document["error_test_ids"]
    )
    if not reported_identifiers.issubset(expected_identifiers):
        raise ShardResultError("Shard result names tests outside its collection")
    if require_success:
        if document["successful"] is not True:
            raise ShardResultError(f"Shard {expected_suite!r} did not pass")
        if any(
            document[field]
            for field in ("failures", "errors", "unexpected_successes")
        ):
            raise ShardResultError(
                f"Shard {expected_suite!r} reports failed outcomes"
            )
    elif (
        document["successful"] is not False
        or not reported_identifiers
        or document["unexpected_successes"]
    ):
        raise ShardResultError(
            f"Shard {expected_suite!r} has no provenance-bearing failure"
        )
    if (
        document["collection_fingerprint_algorithm"]
        != TEST_COLLECTION_FINGERPRINT_ALGORITHM
    ):
        raise ShardResultError("Shard result collection algorithm is not pinned")
    if document["collection_fingerprint"] != expected_fingerprint:
        raise ShardResultError("Shard result collection fingerprint is stale or invalid")

    if expected_backend == "pytest-xdist":
        expected_type = "pytest-xdist-shard-result"
        expected_workers = PYTEST_XDIST_WORKERS
        expected_distribution = PYTEST_XDIST_DISTRIBUTION
        expected_parity = "enforced"
    elif expected_backend == "unittest":
        expected_type = "unittest-shard-result"
        expected_workers = 1
        expected_distribution = "sequential"
        expected_parity = "authoritative"
    else:
        raise ShardResultError(f"Unknown expected backend {expected_backend!r}")
    if (
        document["type"] != expected_type
        or document["backend"] != expected_backend
        or isinstance(document["workers"], bool)
        or not isinstance(document["workers"], int)
        or document["workers"] != expected_workers
        or document["distribution"] != expected_distribution
        or document["collection_parity"] != expected_parity
    ):
        raise ShardResultError("Shard result backend contract is invalid")

    timings = document["module_timings"]
    if not isinstance(timings, list):
        raise ShardResultError("module_timings must be a list")
    if expected_backend == "unittest":
        if timings:
            raise ShardResultError("Sequential shard result must not claim worker timings")
    else:
        observed_modules: list[str] = []
        for timing in timings:
            if not isinstance(timing, Mapping) or set(timing) != {
                "module",
                "worker_elapsed_seconds",
            }:
                raise ShardResultError("Shard module timing has invalid fields")
            module = timing["module"]
            if not isinstance(module, str):
                raise ShardResultError("Shard module timing has an invalid module")
            _nonnegative_number(
                timing["worker_elapsed_seconds"],
                field="worker_elapsed_seconds",
            )
            observed_modules.append(module)
        if (
            len(observed_modules) != len(set(observed_modules))
            or set(observed_modules) != set(modules)
        ):
            raise ShardResultError(
                "Shard module timings do not cover the exact module set"
            )
    return dict(document)
