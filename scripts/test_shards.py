from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import unittest
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
MANIFEST = ROOT / "platform" / "test-shards.json"

for path in (str(ROOT), str(TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)


class TestShardError(ValueError):
    pass


PYTEST_XDIST_WORKERS = 4
PYTEST_XDIST_DISTRIBUTION = "loadfile"
TEST_COLLECTION_FINGERPRINT_ALGORITHM = "canonical-unittest-ids-sha256-v1"
EXPECTED_COLLECTION_ENV = "QUORUNE_EXPECTED_UNITTEST_COLLECTION"
GENERATED_VALIDATION_SHARD = "generated-validation"
BALANCED_FUNCTIONAL_SHARD_PREFIX = "functional-"


def load_manifest(path: Path = MANIFEST) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "execution_order",
        "primary_shards",
        "overlay_suites",
    }:
        raise TestShardError("Test-shard manifest has an invalid top-level shape")
    if value["schema_version"] != 2:
        raise TestShardError("Unsupported test-shard manifest schema")
    for field in ("primary_shards", "overlay_suites"):
        suites = value[field]
        if not isinstance(suites, dict) or not suites:
            raise TestShardError(f"{field} must be a nonempty mapping")
        for name, modules in suites.items():
            if not isinstance(name, str) or not name:
                raise TestShardError(f"{field} contains an invalid suite name")
            if not isinstance(modules, list) or not modules:
                raise TestShardError(f"Suite {name!r} must be a nonempty list")
            if any(
                not isinstance(module, str)
                or not module.startswith("test_")
                or "." in module
                for module in modules
            ):
                raise TestShardError(
                    f"Suite {name!r} contains an invalid test module"
                )
            if len(modules) != len(set(modules)):
                raise TestShardError(f"Suite {name!r} contains duplicates")
            if modules != sorted(modules):
                raise TestShardError(f"Suite {name!r} must be sorted")
    execution_order = value["execution_order"]
    if (
        not isinstance(execution_order, list)
        or not execution_order
        or any(not isinstance(name, str) or not name for name in execution_order)
    ):
        raise TestShardError("execution_order must be a nonempty list of names")
    if len(execution_order) != len(set(execution_order)):
        raise TestShardError("execution_order contains duplicates")
    primary_names = set(value["primary_shards"])
    ordered_names = set(execution_order)
    if primary_names != ordered_names:
        raise TestShardError(
            "execution_order must contain every primary shard exactly once: "
            + json.dumps(
                {
                    "missing": sorted(primary_names - ordered_names),
                    "unknown": sorted(ordered_names - primary_names),
                },
                sort_keys=True,
            )
        )
    return value


def discovered_modules(root: Path = TESTS) -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in root.glob("test_*.py")))


def validate_partition(
    manifest: Mapping,
    *,
    tests_root: Path = TESTS,
) -> dict:
    primary = manifest["primary_shards"]
    assigned = [module for modules in primary.values() for module in modules]
    counts = Counter(assigned)
    duplicates = sorted(module for module, count in counts.items() if count != 1)
    actual = set(discovered_modules(tests_root))
    configured = set(assigned)
    missing = sorted(actual - configured)
    unknown = sorted(configured - actual)
    overlay_unknown = sorted(
        {
            module
            for modules in manifest["overlay_suites"].values()
            for module in modules
        }
        - actual
    )
    if duplicates or missing or unknown or overlay_unknown:
        raise TestShardError(
            json.dumps(
                {
                    "duplicates": duplicates,
                    "missing": missing,
                    "unknown": unknown,
                    "overlay_unknown": overlay_unknown,
                },
                sort_keys=True,
            )
        )
    return {
        "primary_shards": len(primary),
        "test_modules": len(actual),
        "overlay_suites": len(manifest["overlay_suites"]),
    }


def suite_modules(manifest: Mapping, name: str) -> tuple[str, ...]:
    for field in ("primary_shards", "overlay_suites"):
        modules = manifest[field].get(name)
        if modules is not None:
            return tuple(modules)
    raise TestShardError(f"Unknown test suite {name!r}")


def primary_matrix(manifest: Mapping) -> dict:
    validate_partition(manifest)
    return {
        "include": [
            {"shard": name}
            for name in manifest["execution_order"]
        ]
    }


def functional_shards(manifest: Mapping) -> tuple[str, ...]:
    validate_partition(manifest)
    return tuple(
        name
        for name in manifest["execution_order"]
        if name != GENERATED_VALIDATION_SHARD
    )


def load_observed_module_timings(root: Path) -> dict[str, float]:
    """Load one successful Ubuntu loadfile result per functional module."""

    if not root.is_dir():
        raise TestShardError(f"Observed timing root does not exist: {root}")
    found_documents = 0
    timings: dict[str, float] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TestShardError(
                f"Observed timing artifact is unreadable: {path}"
            ) from exc
        if not isinstance(value, dict) or value.get("type") != (
            "pytest-xdist-shard-result"
        ):
            continue
        found_documents += 1
        if (
            value.get("schema_version") != 3
            or value.get("platform") != "ubuntu"
            or value.get("backend") != "pytest-xdist"
            or value.get("workers") != PYTEST_XDIST_WORKERS
            or value.get("distribution") != PYTEST_XDIST_DISTRIBUTION
            or value.get("successful") is not True
        ):
            raise TestShardError(
                f"Observed timing artifact has an unsupported execution shape: {path}"
            )
        modules = value.get("modules")
        rows = value.get("module_timings")
        if (
            not isinstance(modules, list)
            or not modules
            or any(
                type(module) is not str or not module.startswith("test_")
                for module in modules
            )
            or len(modules) != len(set(modules))
            or not isinstance(rows, list)
        ):
            raise TestShardError(
                f"Observed timing artifact has malformed module inventory: {path}"
            )
        document_timings: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "module",
                "worker_elapsed_seconds",
            }:
                raise TestShardError(
                    f"Observed timing artifact has a malformed timing row: {path}"
                )
            module = row["module"]
            seconds = row["worker_elapsed_seconds"]
            if (
                type(module) is not str
                or module not in modules
                or isinstance(seconds, bool)
                or not isinstance(seconds, (int, float))
                or not 0 <= float(seconds)
                or module in document_timings
            ):
                raise TestShardError(
                    f"Observed timing artifact has an invalid module timing: {path}"
                )
            document_timings[module] = float(seconds)
        if set(document_timings) != set(modules):
            raise TestShardError(
                f"Observed timing artifact omits module timings: {path}"
            )
        duplicate = sorted(set(timings).intersection(document_timings))
        if duplicate:
            raise TestShardError(
                "Observed timing artifacts assign modules more than once: "
                + ", ".join(duplicate)
            )
        timings.update(document_timings)
    if found_documents == 0:
        raise TestShardError(
            f"Observed timing root contains no shard results: {root}"
        )
    return timings


def _predicted_worker_loads(
    modules: Sequence[str],
    timings: Mapping[str, float],
) -> tuple[float, ...]:
    loads = [0.0] * PYTEST_XDIST_WORKERS
    for module in sorted(modules, key=lambda item: (-timings[item], item)):
        worker = min(range(len(loads)), key=lambda index: (loads[index], index))
        loads[worker] += timings[module]
    return tuple(sorted(loads, reverse=True))


def rebalance_primary_shards(
    manifest: Mapping,
    observed_timings: Mapping[str, float],
    *,
    estimated_timings: Mapping[str, float] | None = None,
) -> tuple[dict, dict]:
    """Build neutral primary shards while preserving semantic impact suites."""

    validate_partition(manifest)
    original_functional = functional_shards(manifest)
    primary = manifest["primary_shards"]
    generated_modules = tuple(primary[GENERATED_VALIDATION_SHARD])
    functional_modules = tuple(
        sorted(
            module
            for name in original_functional
            for module in primary[name]
        )
    )
    timings = {
        str(module): float(seconds)
        for module, seconds in observed_timings.items()
    }
    estimates = dict(estimated_timings or {})
    for module, seconds in estimates.items():
        if (
            module not in functional_modules
            or isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or float(seconds) < 0
        ):
            raise TestShardError(
                f"Estimated module timing is invalid: {module}={seconds!r}"
            )
        timings[module] = float(seconds)
    missing = sorted(set(functional_modules) - set(timings))
    unknown = sorted(set(timings) - set(functional_modules))
    if missing or unknown:
        raise TestShardError(
            "Observed timing inventory does not match functional modules: "
            + json.dumps({"missing": missing, "unknown": unknown}, sort_keys=True)
        )

    shard_names = tuple(
        f"{BALANCED_FUNCTIONAL_SHARD_PREFIX}{index:02d}"
        for index in range(1, len(original_functional) + 1)
    )
    buckets: dict[str, list[str]] = {name: [] for name in shard_names}
    for module in sorted(functional_modules, key=lambda item: (-timings[item], item)):
        selected = min(
            shard_names,
            key=lambda name: (
                max(
                    _predicted_worker_loads(
                        (*buckets[name], module),
                        timings,
                    )
                ),
                sum(timings[item] for item in buckets[name]),
                len(buckets[name]),
                name,
            ),
        )
        buckets[selected].append(module)

    overlays = {
        str(name): list(modules)
        for name, modules in manifest["overlay_suites"].items()
    }
    if not all(
        name.startswith(BALANCED_FUNCTIONAL_SHARD_PREFIX)
        for name in original_functional
    ):
        for name in original_functional:
            if name in overlays:
                raise TestShardError(
                    f"Semantic overlay would replace existing suite {name!r}"
                )
            overlays[name] = list(primary[name])

    predicted = {
        name: _predicted_worker_loads(buckets[name], timings)
        for name in shard_names
    }
    execution_order = sorted(
        shard_names,
        key=lambda name: (
            -max(predicted[name]),
            -sum(predicted[name]),
            name,
        ),
    )
    execution_order.append(GENERATED_VALIDATION_SHARD)
    balanced = {
        "schema_version": manifest["schema_version"],
        "execution_order": execution_order,
        "primary_shards": {
            **{
                name: sorted(buckets[name])
                for name in shard_names
            },
            GENERATED_VALIDATION_SHARD: list(generated_modules),
        },
        "overlay_suites": {
            name: sorted(modules)
            for name, modules in sorted(overlays.items())
        },
    }
    validate_partition(balanced)
    summary = {
        "functional_shards": len(shard_names),
        "functional_modules": len(functional_modules),
        "estimated_timings": sorted(estimates),
        "predicted_shards": {
            name: {
                "modules": len(buckets[name]),
                "worker_seconds": round(sum(predicted[name]), 3),
                "makespan_seconds": round(max(predicted[name]), 3),
            }
            for name in execution_order
            if name != GENERATED_VALIDATION_SHARD
        },
    }
    return balanced, summary


def load_suite(modules: Iterable[str]) -> unittest.TestSuite:
    names = tuple(dict.fromkeys(modules))
    if not names:
        raise TestShardError("No test modules were selected")
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    errors = []
    for test in _iter_tests(suite):
        if isinstance(test, unittest.loader._FailedTest):
            errors.append(str(test))
    if errors:
        raise TestShardError(f"Test module import failed: {errors}")
    if suite.countTestCases() <= 0:
        raise TestShardError("Selected test modules contain zero tests")
    return suite


def _iter_tests(suite: unittest.TestSuite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _iter_tests(test)
        else:
            yield test


def canonical_test_ids(suite: unittest.TestSuite) -> tuple[str, ...]:
    identifiers = tuple(sorted(test.id() for test in _iter_tests(suite)))
    if len(identifiers) != len(set(identifiers)):
        raise TestShardError("Selected test modules contain duplicate test IDs")
    return identifiers


def test_collection_fingerprint(identifiers: Sequence[str]) -> str:
    digest = hashlib.sha256()
    digest.update((TEST_COLLECTION_FINGERPRINT_ALGORITHM + "\0").encode("ascii"))
    for identifier in identifiers:
        encoded = identifier.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


class PytestShardRecorder:
    """Controller-side observed outcomes and per-module execution timings."""

    def __init__(self) -> None:
        self.seen_items: set[str] = set()
        self.module_seconds: Counter[str] = Counter()
        self.failures = 0
        self.errors = 0
        self.skipped = 0
        self.expected_failures = 0
        self.unexpected_successes = 0
        self.failed_test_ids: set[str] = set()
        self.error_test_ids: set[str] = set()

    def pytest_runtest_logreport(self, report: Any) -> None:
        nodeid = str(report.nodeid)
        self.seen_items.add(nodeid)
        module = Path(nodeid.split("::", 1)[0]).stem
        self.module_seconds[module] += float(getattr(report, "duration", 0.0))
        was_expected_failure = bool(getattr(report, "wasxfail", None))
        if report.skipped:
            if was_expected_failure:
                self.expected_failures += 1
            elif report.when in {"setup", "call"}:
                self.skipped += 1
        elif report.failed:
            if report.when == "call":
                self.failures += 1
                self.failed_test_ids.add(_pytest_nodeid_to_test_id(nodeid))
            else:
                self.errors += 1
                self.error_test_ids.add(_pytest_nodeid_to_test_id(nodeid))
        elif report.passed and was_expected_failure:
            self.unexpected_successes += 1

    def module_timings(self) -> list[dict[str, object]]:
        return [
            {
                "module": module,
                "worker_elapsed_seconds": round(seconds, 3),
            }
            for module, seconds in sorted(
                self.module_seconds.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]


def _pytest_nodeid_to_test_id(nodeid: str) -> str:
    path, *parts = nodeid.split("::")
    if not path.startswith("tests/") or not path.endswith(".py") or not parts:
        raise TestShardError(f"Pytest reported a noncanonical test id: {nodeid}")
    clean_parts = [part.split("[", 1)[0] for part in parts]
    return ".".join(
        [path.removesuffix(".py").replace("/", "."), *clean_parts]
    )


def describe(manifest: Mapping) -> dict:
    validate_partition(manifest)
    result = {}
    for field in ("primary_shards", "overlay_suites"):
        for name, modules in manifest[field].items():
            suite = load_suite(modules)
            result[name] = {
                "kind": field,
                "modules": len(modules),
                "tests": suite.countTestCases(),
            }
    return dict(sorted(result.items()))


def run_modules(
    modules: Sequence[str],
    *,
    verbosity: int = 2,
    suite_name: str | None = None,
    result_json: Path | None = None,
    backend: str = "unittest",
    workers: int = 1,
    platform: str = "local",
) -> bool:
    if backend == "pytest-xdist":
        return run_modules_pytest_xdist(
            modules,
            suite_name=suite_name,
            result_json=result_json,
            workers=workers,
            platform=platform,
        )
    if backend != "unittest":
        raise TestShardError(f"Unsupported test backend {backend!r}")
    if workers != 1:
        raise TestShardError("The unittest backend requires exactly one worker")
    suite = load_suite(modules)
    identifiers = canonical_test_ids(suite)
    configured_test_count = len(identifiers)
    fingerprint = test_collection_fingerprint(identifiers)
    started = perf_counter()
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    duration = round(perf_counter() - started, 3)
    successful = result.wasSuccessful() and result.testsRun > 0
    if result_json is not None:
        document = {
            "schema_version": 3,
            "type": "unittest-shard-result",
            "platform": platform,
            "suite": suite_name,
            "modules": list(modules),
            "configured_test_count": configured_test_count,
            "tests_run": result.testsRun,
            "duration_seconds": duration,
            "successful": successful,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses),
            "failed_test_ids": sorted(test.id() for test, _ in result.failures),
            "error_test_ids": sorted(test.id() for test, _ in result.errors),
            "backend": "unittest",
            "workers": 1,
            "distribution": "sequential",
            "collection_fingerprint_algorithm": (
                TEST_COLLECTION_FINGERPRINT_ALGORITHM
            ),
            "collection_fingerprint": fingerprint,
            "collection_parity": "authoritative",
            "module_timings": [],
        }
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return successful


def run_modules_pytest_xdist(
    modules: Sequence[str],
    *,
    suite_name: str | None,
    result_json: Path | None,
    workers: int,
    platform: str,
) -> bool:
    if isinstance(workers, bool) or workers < 2:
        raise TestShardError("The pytest-xdist backend requires at least two workers")
    try:
        import pytest
    except ImportError as exc:
        raise TestShardError(
            "pytest-xdist backend requires requirements-dev.txt"
        ) from exc

    suite = load_suite(modules)
    identifiers = canonical_test_ids(suite)
    del suite
    configured_test_count = len(identifiers)
    fingerprint = test_collection_fingerprint(identifiers)
    recorder = PytestShardRecorder()
    module_paths = [str(TESTS / f"{module}.py") for module in modules]
    previous_collection = os.environ.get(EXPECTED_COLLECTION_ENV)
    previous_plugin_autoload = os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
    started = perf_counter()
    with TemporaryDirectory(prefix="quorune-pytest-collection-") as raw:
        collection_path = Path(raw) / "expected.json"
        collection_path.write_text(
            json.dumps(list(identifiers), indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.environ[EXPECTED_COLLECTION_ENV] = str(
            collection_path.resolve()
        )
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        try:
            exit_code = int(
                pytest.main(
                    [
                        "-p",
                        "xdist.plugin",
                        "-p",
                        "scripts.pytest_shard_plugin",
                        "-p",
                        "no:cacheprovider",
                        "-q",
                        "-ra",
                        "--tb=short",
                        "-n",
                        str(workers),
                        "--dist",
                        PYTEST_XDIST_DISTRIBUTION,
                        "--max-worker-restart",
                        "0",
                        *module_paths,
                    ],
                    plugins=[recorder],
                )
            )
        finally:
            if previous_collection is None:
                os.environ.pop(EXPECTED_COLLECTION_ENV, None)
            else:
                os.environ[EXPECTED_COLLECTION_ENV] = previous_collection
            if previous_plugin_autoload is None:
                os.environ.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
            else:
                os.environ[
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
                ] = previous_plugin_autoload
    duration = round(perf_counter() - started, 3)
    tests_run = len(recorder.seen_items)
    successful = exit_code == 0 and tests_run == configured_test_count
    if result_json is not None:
        document = {
            "schema_version": 3,
            "type": "pytest-xdist-shard-result",
            "platform": platform,
            "suite": suite_name,
            "modules": list(modules),
            "configured_test_count": configured_test_count,
            "tests_run": tests_run,
            "duration_seconds": duration,
            "successful": successful,
            "failures": recorder.failures,
            "errors": recorder.errors,
            "skipped": recorder.skipped,
            "expected_failures": recorder.expected_failures,
            "unexpected_successes": recorder.unexpected_successes,
            "failed_test_ids": sorted(recorder.failed_test_ids),
            "error_test_ids": sorted(recorder.error_test_ids),
            "backend": "pytest-xdist",
            "workers": workers,
            "distribution": PYTEST_XDIST_DISTRIBUTION,
            "collection_fingerprint_algorithm": (
                TEST_COLLECTION_FINGERPRINT_ALGORITHM
            ),
            "collection_fingerprint": fingerprint,
            "collection_parity": "enforced",
            "module_timings": recorder.module_timings(),
        }
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return successful


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate, describe, or run deterministic Python test shards"
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("describe")
    rebalance = subparsers.add_parser("rebalance")
    rebalance.add_argument("--results-root", required=True)
    rebalance.add_argument(
        "--estimate",
        action="append",
        default=[],
        metavar="MODULE=SECONDS",
    )
    rebalance.add_argument("--write", action="store_true")
    run = subparsers.add_parser("run")
    run.add_argument("suite")
    run.add_argument("--result-json")
    run.add_argument(
        "--backend",
        choices=("unittest", "pytest-xdist"),
        default="unittest",
    )
    run.add_argument("--workers", type=int)
    run.add_argument("--platform", default="local")
    modules = subparsers.add_parser("run-modules")
    modules.add_argument("module", nargs="+")
    modules.add_argument("--result-json")
    modules.add_argument("--suite-name", default="run-modules")
    modules.add_argument(
        "--backend",
        choices=("unittest", "pytest-xdist"),
        default="unittest",
    )
    modules.add_argument("--workers", type=int)
    modules.add_argument("--platform", default="local")
    args = parser.parse_args()

    try:
        manifest = load_manifest()
        summary = validate_partition(manifest)
        if args.operation == "validate":
            print(json.dumps({"ok": True, **summary}, sort_keys=True))
            return 0
        if args.operation == "describe":
            print(json.dumps(describe(manifest), indent=2, sort_keys=True))
            return 0
        if args.operation == "rebalance":
            estimates: dict[str, float] = {}
            for raw_estimate in args.estimate:
                module, separator, raw_seconds = raw_estimate.partition("=")
                if (
                    not separator
                    or not module.startswith("test_")
                    or module in estimates
                ):
                    raise TestShardError(
                        f"Invalid module timing estimate: {raw_estimate!r}"
                    )
                try:
                    estimates[module] = float(raw_seconds)
                except ValueError as exc:
                    raise TestShardError(
                        f"Invalid module timing estimate: {raw_estimate!r}"
                    ) from exc
            balanced, summary = rebalance_primary_shards(
                manifest,
                load_observed_module_timings(Path(args.results_root)),
                estimated_timings=estimates,
            )
            if args.write:
                MANIFEST.write_text(
                    json.dumps(balanced, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
            print(
                json.dumps(
                    {"ok": True, "written": bool(args.write), **summary},
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        selected = (
            suite_modules(manifest, args.suite)
            if args.operation == "run"
            else tuple(args.module)
        )
        suite_name = args.suite if args.operation == "run" else args.suite_name
        result_json = Path(args.result_json) if args.result_json else None
        workers = (
            args.workers
            if args.workers is not None
            else (
                PYTEST_XDIST_WORKERS
                if args.backend == "pytest-xdist"
                else 1
            )
        )
        return (
            0
            if run_modules(
                selected,
                suite_name=suite_name,
                result_json=result_json,
                backend=args.backend,
                workers=workers,
                platform=args.platform,
            )
            else 1
        )
    except TestShardError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
