from __future__ import annotations

import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from tests.common import ROOT
from scripts.ci_metrics import build_metrics, markdown
from scripts.ci_plan import (
    browser_matrix,
    ci_concurrency_budget,
    python_matrix,
    workflow_job_ids,
)
from scripts.nightly_ci import nightly_concurrency_budget, nightly_python_matrix
from scripts.shard_result_validation import suite_expectation
from scripts.shard_result_validation import (
    ShardResultError,
    validate_result_document,
)
from scripts.test_shards import load_manifest, primary_matrix, suite_modules
from scripts.verify_ci_needs import failed_dependencies
from scripts.verify_windows_ci import (
    WindowsCertificationError,
    expected_suites,
    validate_dependencies,
    validate_results,
)
from scripts.verify_nightly_ci import (
    NightlyCertificationError,
    validate_dependencies as validate_nightly_dependencies,
    validate_results as validate_nightly_results,
)


class CiPipelineTests(unittest.TestCase):
    @staticmethod
    def _windows_result(suite: str, *, tests_run: int = 3) -> dict:
        modules, exact_count, fingerprint = suite_expectation(suite)
        count = 0 if tests_run == 0 else exact_count
        backend = "unittest" if suite == "generated-validation" else "pytest-xdist"
        return {
            "schema_version": 2,
            "type": (
                "unittest-shard-result"
                if backend == "unittest"
                else "pytest-xdist-shard-result"
            ),
            "platform": "windows",
            "suite": suite,
            "modules": list(modules),
            "configured_test_count": count,
            "tests_run": count,
            "duration_seconds": 25.0,
            "successful": True,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "expected_failures": 0,
            "unexpected_successes": 0,
            "backend": backend,
            "workers": 1 if backend == "unittest" else 4,
            "distribution": "sequential" if backend == "unittest" else "loadfile",
            "collection_fingerprint_algorithm": "canonical-unittest-ids-sha256-v1",
            "collection_fingerprint": fingerprint,
            "collection_parity": "authoritative" if backend == "unittest" else "enforced",
            "module_timings": (
                []
                if backend == "unittest"
                else [
                    {"module": module, "worker_elapsed_seconds": 0.1}
                    for module in modules
                ]
            ),
        }

    @staticmethod
    def _python_result(suite: str, *, tests_run: int = 3) -> dict:
        modules, exact_count, fingerprint = suite_expectation(suite)
        return {
            "schema_version": 2,
            "type": "pytest-xdist-shard-result",
            "platform": "ubuntu",
            "suite": suite,
            "modules": list(modules),
            "configured_test_count": exact_count,
            "tests_run": exact_count,
            "duration_seconds": 12.5,
            "successful": True,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "expected_failures": 0,
            "unexpected_successes": 0,
            "backend": "pytest-xdist",
            "workers": 4,
            "distribution": "loadfile",
            "collection_fingerprint_algorithm": "canonical-unittest-ids-sha256-v1",
            "collection_fingerprint": fingerprint,
            "collection_parity": "enforced",
            "module_timings": [
                {
                    "module": module,
                    "worker_elapsed_seconds": 10.0 / len(modules),
                }
                for module in modules
            ],
        }

    def test_browser_matrix_uses_one_smoke_or_three_isolated_measured_groups(self):
        smoke = browser_matrix(False)["include"]
        full = browser_matrix(True)["include"]
        self.assertEqual(1, len(smoke))
        self.assertEqual("smoke", smoke[0]["group"])
        self.assertEqual(3, len(full))
        self.assertEqual(
            {"lifecycle", "rules", "soak"},
            {row["group"] for row in full},
        )
        self.assertEqual(3, len({row["server_port"] for row in full}))
        self.assertEqual(3, len({row["web_port"] for row in full}))
        self.assertEqual(3, len({row["grep"] for row in full}))

    def test_pr_matrix_preserves_two_jobs_of_public_recovery_headroom(self):
        expected = {
            (False, False): (12, 1, 1, 17, 3),
            (True, False): (11, 1, 3, 18, 2),
            (False, True): (6, 8, 1, 18, 2),
            (True, True): (5, 7, 3, 18, 2),
        }
        for (browser_full, windows_full), values in expected.items():
            budget = ci_concurrency_budget(
                browser_full=browser_full,
                windows_full=windows_full,
            )
            observed = (
                budget["python_max_parallel"],
                budget["windows_max_parallel"],
                budget["browser_max_parallel"],
                budget["peak_jobs"],
                budget["headroom"],
            )
            self.assertEqual(values, observed)
            self.assertEqual(20, budget["public_job_limit"])
        self.assertEqual(
            [
                shard
                for shard in load_manifest()["execution_order"]
                if shard != "generated-validation"
            ],
            [row["shard"] for row in python_matrix()["include"]],
        )
        self.assertIn("python", workflow_job_ids())
        with TemporaryDirectory() as raw:
            changed = Path(raw) / "ci.yml"
            changed.write_text("jobs:\n  unbudgeted_job:\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "budget review"):
                workflow_job_ids(changed)

    def test_nightly_matrix_is_slow_first_cross_platform_and_budgeted(self):
        manifest = load_manifest()
        rows = nightly_python_matrix()["include"]
        self.assertEqual(2 * len(manifest["execution_order"]), len(rows))
        self.assertEqual(
            [
                (platform, shard)
                for shard in manifest["execution_order"]
                for platform in ("ubuntu", "windows")
            ],
            [(row["platform"], row["shard"]) for row in rows],
        )
        budget = nightly_concurrency_budget()
        self.assertEqual(6, budget["python_max_parallel"])
        self.assertEqual(15, budget["peak_jobs"])
        self.assertEqual(5, budget["headroom"])

    def test_certification_fails_for_any_non_success_dependency(self):
        self.assertEqual(
            ("browser", "windows"),
            failed_dependencies(
                {
                    "python": {"result": "success"},
                    "browser": {"result": "failure"},
                    "windows": {"result": "cancelled"},
                }
            ),
        )
        self.assertEqual((), failed_dependencies({"python": {"result": "success"}}))

    def test_windows_certification_requires_exact_mode_dependencies(self):
        validate_dependencies(
            {
                "plan": {"result": "success"},
                "windows_compatibility": {"result": "skipped"},
                "windows_full": {"result": "success"},
                "windows_package": {"result": "success"},
            },
            full=True,
        )
        with self.assertRaises(WindowsCertificationError):
            validate_dependencies(
                {
                    "plan": {"result": "success"},
                    "windows_compatibility": {"result": "skipped"},
                    "windows_full": {"result": "skipped"},
                    "windows_package": {"result": "success"},
                },
                full=True,
            )

    def test_windows_full_results_cover_every_primary_shard_and_are_nonempty(self):
        self.assertEqual(
            tuple(load_manifest()["execution_order"]),
            expected_suites(full=True),
        )
        with TemporaryDirectory() as raw:
            root = Path(raw)
            for suite in expected_suites(full=True):
                path = root / suite / "result.json"
                path.parent.mkdir(parents=True)
                path.write_text(
                    json.dumps(self._windows_result(suite)), encoding="utf-8"
                )
            summary = validate_results(root, full=True)
            self.assertEqual(len(primary_matrix(load_manifest())["include"]), summary["suites"])
            self.assertGreater(summary["tests_run"], 0)

    def test_windows_results_fail_closed_for_missing_or_zero_test_shard(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            result = self._windows_result("windows-compat", tests_run=0)
            (root / "result.json").write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(WindowsCertificationError, "zero tests"):
                validate_results(root, full=False)
        with TemporaryDirectory() as raw:
            with self.assertRaisesRegex(WindowsCertificationError, "missing_results"):
                validate_results(Path(raw), full=False)

    def test_shard_results_bind_exact_collection_backend_and_platform(self):
        valid = self._windows_result("windows-compat")
        validated = validate_result_document(
            valid,
            expected_suite="windows-compat",
            expected_platform="windows",
            expected_backend="pytest-xdist",
        )
        self.assertEqual("windows", validated["platform"])
        for field, value, message in (
            ("tests_run", True, "exact nonnegative integer"),
            ("workers", True, "backend contract"),
            ("duration_seconds", float("nan"), "nonnegative number"),
            ("collection_fingerprint", "0" * 64, "fingerprint"),
            ("platform", "ubuntu", "platform"),
        ):
            malformed = dict(valid)
            malformed[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(ShardResultError, message):
                    validate_result_document(
                        malformed,
                        expected_suite="windows-compat",
                        expected_platform="windows",
                        expected_backend="pytest-xdist",
                    )
        missing_timing = dict(valid)
        missing_timing["module_timings"] = valid["module_timings"][:-1]
        with self.assertRaisesRegex(ShardResultError, "exact module set"):
            validate_result_document(
                missing_timing,
                expected_suite="windows-compat",
                expected_platform="windows",
                expected_backend="pytest-xdist",
            )

    def test_nightly_certification_requires_every_os_shard_and_dependency(self):
        dependency_names = {
            "plan",
            "python",
            "browser",
            "properties",
            "mutation-and-soak",
            "corpus",
            "security",
        }
        validate_nightly_dependencies(
            {name: {"result": "success"} for name in dependency_names}
        )
        with self.assertRaisesRegex(NightlyCertificationError, "did not all pass"):
            validate_nightly_dependencies(
                {
                    name: {
                        "result": "failure" if name == "browser" else "success"
                    }
                    for name in dependency_names
                }
            )
        with TemporaryDirectory() as raw:
            root = Path(raw)
            for suite in load_manifest()["execution_order"]:
                for platform in ("ubuntu", "windows"):
                    document = self._windows_result(suite)
                    document["platform"] = platform
                    path = root / platform / suite / "result.json"
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps(document), encoding="utf-8")
            summary = validate_nightly_results(root)
            self.assertEqual(2 * len(load_manifest()["execution_order"]), summary["assignments"])
            (root / "ubuntu" / "core-domain" / "result.json").unlink()
            with self.assertRaisesRegex(NightlyCertificationError, "incomplete"):
                validate_nightly_results(root)

    def test_metrics_report_observed_values_without_estimates(self):
        metrics = build_metrics(
            {
                "id": 42,
                "head_sha": "abc",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-08-03T12:00:00Z",
            },
            {
                "jobs": [
                    {
                        "name": "PR / Python / core-domain",
                        "conclusion": "success",
                        "started_at": "2026-08-03T12:00:05Z",
                        "completed_at": "2026-08-03T12:02:05Z",
                        "steps": [
                            {
                                "name": "Run shard",
                                "conclusion": "success",
                                "started_at": "2026-08-03T12:00:20Z",
                                "completed_at": "2026-08-03T12:02:00Z",
                            }
                        ],
                    },
                    {
                        "name": "PR / Windows / core-domain",
                        "conclusion": "success",
                        "started_at": "2026-08-03T12:00:10Z",
                        "completed_at": "2026-08-03T12:01:10Z",
                        "steps": [
                            {
                                "name": "Install package and test dependencies",
                                "conclusion": "success",
                                "started_at": "2026-08-03T12:00:10Z",
                                "completed_at": "2026-08-03T12:00:20Z",
                            },
                            {
                                "name": "Run full Windows shard",
                                "conclusion": "success",
                                "started_at": "2026-08-03T12:00:20Z",
                                "completed_at": "2026-08-03T12:01:00Z",
                            },
                        ],
                    },
                    {
                        "name": "PR / Windows / package",
                        "conclusion": "success",
                        "started_at": "2026-08-03T12:00:08Z",
                        "completed_at": "2026-08-03T12:00:48Z",
                        "steps": [
                            {
                                "name": "Build and verify Windows wheel",
                                "conclusion": "success",
                                "started_at": "2026-08-03T12:00:20Z",
                                "completed_at": "2026-08-03T12:00:45Z",
                            }
                        ],
                    },
                    {
                        "name": "PR / Windows / casting-costs-mana",
                        "conclusion": "success",
                        # GitHub may mark a matrix job started while it is still
                        # waiting for the strategy's runner slot.
                        "started_at": "2026-08-03T12:00:11Z",
                        "completed_at": "2026-08-03T12:02:00Z",
                        "steps": [
                            {
                                "name": "Install package and test dependencies",
                                "conclusion": "success",
                                "started_at": "2026-08-03T12:01:00Z",
                                "completed_at": "2026-08-03T12:01:10Z",
                            },
                            {
                                "name": "Run full Windows shard",
                                "conclusion": "success",
                                "started_at": "2026-08-03T12:01:10Z",
                                "completed_at": "2026-08-03T12:01:40Z",
                            },
                        ],
                    },
                ]
            },
            [
                (
                    "rules",
                    {
                        "suites": [
                            {
                                "title": "four-player.spec.ts",
                                "specs": [
                                    {
                                        "title": "rules journey",
                                        "file": "four-player.spec.ts",
                                        "tests": [
                                            {
                                                "annotations": [
                                                    {
                                                        "type": "commander-journey-metrics",
                                                        "description": json.dumps(
                                                            {
                                                                "browser_contexts": 2,
                                                                "accepted_commands": 12,
                                                                "authoritative_revisions": 18,
                                                                "derived_review_seconds": 0.0,
                                                            }
                                                        ),
                                                    }
                                                ],
                                                "results": [
                                                    {
                                                        "status": "failed",
                                                        "duration": 1500,
                                                        "errors": [
                                                            {
                                                                "message": "expect timeout"
                                                            }
                                                        ],
                                                    },
                                                    {
                                                        "status": "passed",
                                                        "duration": 1250,
                                                        "errors": [],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ]
                    },
                )
            ],
            [
                self._windows_result("core-domain"),
                self._windows_result("casting-costs-mana"),
            ],
            [self._python_result("core-domain")],
        )
        self.assertEqual(5.0, metrics["queue_seconds"])
        self.assertEqual(125.0, metrics["critical_path_seconds_observed"])
        self.assertIsNone(metrics["cache_hit_rate"])
        self.assertEqual(100.0, metrics["jobs"][0]["steps"][0]["duration_seconds"])
        journey = metrics["browser_journeys"][0]
        self.assertEqual("rules", journey["group"])
        self.assertEqual(1, journey["retry_count"])
        self.assertEqual("none", journey["failure_classification"])
        self.assertEqual(12, journey["game_metrics"]["accepted_commands"])
        windows = metrics["windows"]
        self.assertEqual(100.0, windows["critical_path_seconds_observed"])
        self.assertEqual(1, windows["max_runner_concurrency_observed"])
        self.assertEqual(25.0, windows["package_duration_seconds"])
        self.assertEqual(
            suite_expectation("casting-costs-mana")[1],
            windows["shards"][0]["test_count"],
        )
        self.assertEqual(10.0, windows["shards"][0]["setup_duration_seconds"])
        python = metrics["python"]
        self.assertEqual(4, python["shards"][0]["workers"])
        self.assertEqual("pytest-xdist", python["shards"][0]["backend"])
        self.assertEqual(12.5, python["shards"][0]["test_duration_seconds"])
        self.assertIn("unavailable", markdown(metrics))
        self.assertIn("rules journey", markdown(metrics))
        self.assertIn("core-domain", markdown(metrics))

    def test_workflows_separate_pr_main_and_nightly_responsibilities(self):
        pr = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        main = (ROOT / ".github/workflows/main-smoke.yml").read_text(
            encoding="utf-8"
        )
        nightly = (ROOT / ".github/workflows/nightly.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "cancel-in-progress: true",
            pr,
        )
        self.assertIn(
            "${{ github.event.action == 'edited' && 'metadata' || 'source' }}",
            pr,
        )
        self.assertIn("PR / Certification", pr)
        self.assertIn("opened, synchronize, reopened, edited", pr)
        self.assertNotIn("ready_for_review", pr)
        self.assertIn("python scripts/validate_pr_body.py", pr)
        self.assertIn("certification_receipt.py can-reuse-pr", pr)
        self.assertIn("--event-action \"${{ github.event.action }}\"", pr)
        self.assertIn("--wait-seconds \"21600\"", pr)
        self.assertGreaterEqual(
            pr.count("needs.plan.outputs.reuse_certification"),
            12,
        )
        self.assertLess(
            pr.index("python scripts/validate_pr_body.py"),
            pr.index("python scripts/ci_plan.py"),
        )
        self.assertIn(
            "python scripts/build_test_database.py validate-ci-dependencies", pr
        )
        self.assertLess(
            pr.index("python scripts/build_test_database.py validate-ci-dependencies"),
            pr.index("\n  python:"),
        )
        self.assertIn("fromJSON(needs.plan.outputs.browser_matrix)", pr)
        self.assertIn("fromJSON(needs.plan.outputs.windows_matrix)", pr)
        self.assertIn("needs.plan.outputs.browser_focus_grep", pr)
        self.assertIn("Run focused browser journeys for affected rules", pr)
        self.assertIn('npx playwright test --grep "$MTG_BROWSER_GROUP_GREP"', pr)
        self.assertNotIn("--shard=", pr)
        self.assertIn("actions/download-artifact@v4", pr)
        self.assertIn("--browser-report-dir local/browser-results", pr)
        self.assertIn("--windows-report-dir local/windows-results", pr)
        self.assertIn("--python-report-dir local/python-results", pr)
        self.assertIn("scripts/test_shards.py run", pr)
        self.assertIn("--backend pytest-xdist", pr)
        self.assertIn("--workers 4", pr)
        self.assertIn("python-results-${{ matrix.shard }}", pr)
        self.assertIn(
            "max-parallel: ${{ fromJSON(needs.plan.outputs.python_max_parallel) }}",
            pr,
        )
        generated = pr.split("\n  generated:", 1)[1].split("\n  package:", 1)[0]
        package = pr.split("\n  package:", 1)[1].split("\n  windows_compatibility:", 1)[0]
        windows_full = pr.split("\n  windows_full:", 1)[1].split("\n  windows_package:", 1)[0]
        self.assertIn("MTG_CARD_DB: data/test-ci.sqlite3", generated)
        self.assertIn("scripts/build_test_database.py build-ci", generated)
        self.assertIn("scripts/build_test_database.py validate-ci", generated)
        self.assertIn("scripts/finalize_generated.py --check", generated)
        self.assertNotIn(
            "scripts/update_reusable_piece_matrix.py --check", generated
        )
        self.assertIn("python -m pip install -e .", package)
        self.assertIn("needs.plan.outputs.windows_max_parallel", windows_full)
        self.assertIn("validate-ci-dependencies", windows_full)
        self.assertIn('python scripts/test_shards.py run "${{ matrix.shard }}"', windows_full)
        self.assertIn("--backend pytest-xdist", windows_full)
        self.assertIn("--platform windows", windows_full)
        self.assertNotIn("unittest discover", windows_full)
        self.assertIn("PR / Windows Certification", pr)
        self.assertIn("windows_certification", pr)
        self.assertIn("branches: [\"main\"]", main)
        self.assertIn("actions: read", main)
        self.assertIn("pull-requests: read", main)
        self.assertIn("certification_receipt.py verify-main", main)
        self.assertIn("validate-ci-dependencies", main)
        self.assertNotIn("test_*.py", main)
        self.assertIn("schedule:", nightly)
        self.assertIn("python scripts/nightly_ci.py", nightly)
        self.assertIn("python scripts/verify_nightly_ci.py", nightly)
        self.assertIn("needs.plan.outputs.python_max_parallel", nightly)
        self.assertIn("--backend pytest-xdist", nightly)
        self.assertIn("MTG_PROPERTY_TRANSITIONS: \"33334\"", nightly)
        self.assertGreaterEqual(nightly.count("validate-ci-dependencies"), 4)
        self.assertNotIn("unittest discover", nightly)
        combined = "\n".join((pr, main, nightly))
        self.assertNotIn("--fixture tests/fixtures/", combined)
        for line in combined.splitlines():
            if (
                "scripts/build_test_database.py" in line
                and "validate-ci" not in line
            ):
                self.assertIn("build-ci", line)

    def test_browser_smoke_is_headless_and_never_opens_report(self):
        package = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))
        self.assertIn("--grep @smoke", package["scripts"]["e2e:smoke"])
        config = (ROOT / "web/playwright.config.ts").read_text(encoding="utf-8")
        self.assertIn("headless: true", config)
        self.assertIn('open: "never"', config)

    def test_cloud_generated_workflow_is_read_only_parallel_and_downloadable(self):
        cloud = (
            ROOT / ".github/workflows/generated-artifacts.yml"
        ).read_text(encoding="utf-8")
        jobs_text = cloud.split("\njobs:\n", 1)[1]
        jobs = set(
            re.findall(
                r"^  ([a-z][a-z0-9_]*):$",
                jobs_text,
                flags=re.MULTILINE,
            )
        )

        self.assertEqual(
            {
                "plan",
                "database",
                "foundations",
                "corpus",
                "fanout",
                "architecture",
                "reusable",
                "compact",
                "scheduler",
                "bundle",
            },
            jobs,
        )
        self.assertIn('branches: ["main"]', cloud)
        self.assertIn("pull_request:", cloud)
        self.assertIn("types: [opened, synchronize, reopened]", cloud)
        self.assertNotIn("ready_for_review", cloud)
        self.assertIn("github.event.pull_request.head.sha", cloud)
        self.assertIn("workflow_dispatch:", cloud)
        self.assertIn("contents: read", cloud)
        self.assertNotIn("contents: write", cloud)
        self.assertNotIn("pull-requests: write", cloud)
        self.assertIn("--from-rules-manifest", cloud)
        self.assertIn("max-parallel: 5", cloud)
        self.assertIn("max-parallel: 2", cloud)
        self.assertIn("owner: [card-unlock-frontier, platform-status]", cloud)
        self.assertIn("--owner compiler-corpus-coverage", cloud)
        self.assertIn("--owner work-selection-cohort-measurements", cloud)
        self.assertIn("--owner reusable-pieces", cloud)
        self.assertIn("actions/download-artifact@v4", cloud)
        self.assertIn("merge-multiple: true", cloud)
        self.assertIn("scripts/finalize_generated.py --assemble", cloud)
        self.assertIn("--phase pre-corpus", cloud)
        self.assertIn("generated-owner-v1-", cloud)
        self.assertIn("find_reusable_workflow_artifact.py", cloud)
        self.assertIn("generated-owner-cache-v1-", cloud)
        self.assertIn("pinned-card-database-v1-", cloud)
        self.assertIn("--affected-owners-json", cloud)
        self.assertIn("--require-owner-receipts", cloud)
        self.assertIn("cloud-generated-${{ needs.plan.outputs.source_sha }}", cloud)
        self.assertIn("git diff --exit-code --", cloud)

        def job(name: str, following: str | None) -> str:
            tail = cloud.split(f"\n  {name}:\n", 1)[1]
            if following is None:
                return tail
            return tail.split(f"\n  {following}:\n", 1)[0]

        jobs_in_order = [
            "plan",
            "database",
            "foundations",
            "corpus",
            "fanout",
            "architecture",
            "reusable",
            "compact",
            "scheduler",
            "bundle",
        ]
        sections = {
            name: job(
                name,
                jobs_in_order[index + 1]
                if index + 1 < len(jobs_in_order)
                else None,
            )
            for index, name in enumerate(jobs_in_order)
        }
        for name in (
            "database",
            "foundations",
            "corpus",
            "fanout",
            "architecture",
            "reusable",
            "compact",
        ):
            self.assertIn("fetch-depth: 1", sections[name])
            self.assertNotIn("fetch-depth: 0", sections[name])
        for name in ("plan", "scheduler"):
            self.assertIn("fetch-depth: 0", sections[name])
            self.assertIn("filter: blob:none", sections[name])
        self.assertIn("fetch-depth: 0", sections["bundle"])
        self.assertNotIn("filter: blob:none", sections["bundle"])


if __name__ == "__main__":
    unittest.main()
