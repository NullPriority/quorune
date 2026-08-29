from __future__ import annotations

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.main_health import (
    MainHealthError,
    _generated_outputs_for_recovery,
    failed_main_python_jobs,
    validate_recovery_browser_changes,
    validate_recovery_test_changes,
    verify_fix_forward_base,
    verify_main_health,
    verify_recovery_route,
)


BASE_SOURCE = """\
import unittest

class RecoveryTests(unittest.TestCase):
    def test_case(self):
        fixture = 1
        self.assertEqual(1, fixture)

    def test_other(self):
        self.assertTrue(True)
"""

HEAD_SOURCE = BASE_SOURCE.replace("fixture = 1", "fixture = int('1')")
FAILED_ID = "tests.test_fixture.RecoveryTests.test_case"

BROWSER_TITLE = (
    "@browser-soak @natural-winner @persistence "
    "a trusted browser duel reaches a natural commander-damage winner"
)
BROWSER_SOURCE = f"""\
async function advance(): Promise<boolean> {{
  return false;
}}

test(\"{BROWSER_TITLE}\", async () => {{
  expect(await advance()).toBe(false);
}});
"""
BROWSER_HEAD_SOURCE = BROWSER_SOURCE.replace("return false", "return true")


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
    ).stdout.strip()


class MainRecoveryTests(unittest.TestCase):
    def test_label_requests_but_does_not_authorize_recovery(self):
        red = {
            "workflow_runs": [
                {
                    "id": 42,
                    "path": ".github/workflows/main-broad.yml",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "failure",
                }
            ]
        }
        self.assertEqual(
            "red-recovery-requested",
            verify_main_health(red, allow_recovery=True)["state"],
        )
        green = {
            "workflow_runs": [{**red["workflow_runs"][0], "conclusion": "success"}]
        }
        with self.assertRaisesRegex(MainHealthError, "while main is green"):
            verify_main_health(green, allow_recovery=True)

    def test_fix_forward_requires_the_exact_failed_main_base(self):
        head = "a" * 40
        red = {
            "workflow_runs": [
                {
                    "id": 42,
                    "path": ".github/workflows/main-broad.yml",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "failure",
                    "head_sha": head,
                }
            ]
        }
        self.assertEqual(
            "red-fix-forward-requested",
            verify_main_health(
                red,
                allow_recovery=False,
                allow_fix_forward=True,
            )["state"],
        )
        event = {"pull_request": {"base": {"sha": head}}}
        self.assertEqual(head, verify_fix_forward_base(runs=red, event=event))
        event["pull_request"]["base"]["sha"] = "b" * 40
        with self.assertRaisesRegex(MainHealthError, "does not exactly match"):
            verify_fix_forward_base(runs=red, event=event)
        with self.assertRaisesRegex(MainHealthError, "mutually exclusive"):
            verify_main_health(
                red,
                allow_recovery=True,
                allow_fix_forward=True,
            )

    def test_only_immutable_ubuntu_python_failures_can_be_narrowed(self):
        jobs = {
            "jobs": [
                {
                    "id": 700,
                    "name": "Main / Broad / Python / ubuntu / functional-05",
                    "conclusion": "failure",
                },
                {
                    "id": 701,
                    "name": "Main / Broad regression",
                    "conclusion": "failure",
                },
            ]
        }
        self.assertEqual(700, failed_main_python_jobs(jobs)[0].job_id)
        jobs["jobs"].append(
            {
                "id": 702,
                "name": "Main / Broad / Browser / rules",
                "conclusion": "failure",
            }
        )
        with self.assertRaisesRegex(MainHealthError, "non-Python failures"):
            failed_main_python_jobs(jobs)

    def test_fixture_change_preserves_tests_decorators_and_assertions(self):
        self.assertEqual(
            ("test_fixture",),
            validate_recovery_test_changes(
                base_sources={"test_fixture": BASE_SOURCE},
                head_sources={"test_fixture": HEAD_SOURCE},
                failed_test_ids=(FAILED_ID,),
            ),
        )
        weakened = HEAD_SOURCE.replace(
            "self.assertEqual(1, fixture)", "self.assertTrue(True)"
        )
        with self.assertRaisesRegex(MainHealthError, "assertions"):
            validate_recovery_test_changes(
                base_sources={"test_fixture": BASE_SOURCE},
                head_sources={"test_fixture": weakened},
                failed_test_ids=(FAILED_ID,),
            )
        changed_other = HEAD_SOURCE.replace(
            "self.assertTrue(True)", "self.assertFalse(False)"
        )
        with self.assertRaisesRegex(MainHealthError, "nonfailing"):
            validate_recovery_test_changes(
                base_sources={"test_fixture": BASE_SOURCE},
                head_sources={"test_fixture": changed_other},
                failed_test_ids=(FAILED_ID,),
            )

    def test_browser_driver_change_preserves_test_body_tags_and_assertions(self):
        path = "web/tests/four-player.spec.ts"
        self.assertEqual(
            (path,),
            validate_recovery_browser_changes(
                base_sources={path: BROWSER_SOURCE},
                head_sources={path: BROWSER_HEAD_SOURCE},
            ),
        )
        weakened = BROWSER_HEAD_SOURCE.replace(
            "expect(await advance()).toBe(false);",
            "expect(true).toBe(true);",
        )
        with self.assertRaisesRegex(MainHealthError, "tests, tags, or assertions"):
            validate_recovery_browser_changes(
                base_sources={path: BROWSER_SOURCE},
                head_sources={path: weakened},
            )

    def test_recovery_allows_only_automatic_generated_owner_outputs(self):
        owners = [
            SimpleNamespace(
                id="automatic-owner",
                write_policy="automatic",
                outputs=("coverage/automatic.json",),
            ),
            SimpleNamespace(
                id="database-owner",
                write_policy="database",
                outputs=("coverage/database.json",),
            ),
        ]
        with (
            patch(
                "scripts.generated_owner_cache.affected_owner_plan",
                return_value={
                    "reason": "declared-input-change",
                    "database_required": True,
                    "owners": ["automatic-owner", "database-owner"],
                },
            ),
            patch("scripts.generated_artifacts.load_manifest", return_value=owners),
        ):
            selected, outputs = _generated_outputs_for_recovery(
                root=Path.cwd(), base_ref="base"
            )
        self.assertEqual(("automatic-owner",), selected)
        self.assertEqual(("coverage/automatic.json",), outputs)

    def test_full_route_binds_run_job_artifact_test_and_exact_diff(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            _git(root, "init", "-q")
            _git(root, "config", "user.email", "ci@example.invalid")
            _git(root, "config", "user.name", "CI")
            tests = root / "tests"
            tests.mkdir()
            path = tests / "test_fixture.py"
            path.write_text(BASE_SOURCE, encoding="utf-8")
            _git(root, "add", "tests/test_fixture.py")
            _git(root, "commit", "-qm", "base")
            base = _git(root, "rev-parse", "HEAD")
            path.write_text(HEAD_SOURCE, encoding="utf-8")
            _git(root, "add", "tests/test_fixture.py")
            _git(root, "commit", "-qm", "head")
            head = _git(root, "rev-parse", "HEAD")
            runs = {
                "workflow_runs": [
                    {
                        "id": 42,
                        "path": ".github/workflows/main-broad.yml",
                        "event": "push",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_branch": "main",
                        "head_sha": base,
                    }
                ]
            }
            jobs = {
                "jobs": [
                    {
                        "id": 700,
                        "name": "Main / Broad / Python / ubuntu / functional-05",
                        "conclusion": "failure",
                    }
                ]
            }
            document = {
                "type": "python-shard-result",
                "platform": "ubuntu",
                "suite": "functional-05",
                "successful": False,
                "failed_test_ids": [FAILED_ID],
                "error_test_ids": [],
            }
            with (
                patch(
                    "scripts.main_health._json_documents",
                    return_value=((root / "python.json", document),),
                ),
                patch(
                    "scripts.shard_result_validation.validate_result_document",
                    return_value=document,
                ),
                patch(
                    "scripts.main_health._generated_outputs_for_recovery",
                    return_value=((), ()),
                ),
            ):
                plan = verify_recovery_route(
                    runs=runs,
                    jobs=jobs,
                    results_dir=root,
                    base_sha=base,
                    head_sha=head,
                    root=root,
                )
            self.assertEqual(42, plan["main_run_id"])
            self.assertEqual(700, plan["failed_jobs"][0]["id"])
            self.assertEqual([FAILED_ID], plan["failed_test_ids"])
            self.assertEqual(["test_fixture"], plan["test_modules"])
            self.assertEqual([], plan["browser_focus_patterns"])

    def test_browser_route_binds_report_and_runs_only_the_failed_title(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            _git(root, "init", "-q")
            _git(root, "config", "user.email", "ci@example.invalid")
            _git(root, "config", "user.name", "CI")
            path = root / "web" / "tests" / "four-player.spec.ts"
            path.parent.mkdir(parents=True)
            path.write_text(BROWSER_SOURCE, encoding="utf-8")
            _git(root, "add", "web/tests/four-player.spec.ts")
            _git(root, "commit", "-qm", "base")
            base = _git(root, "rev-parse", "HEAD")
            path.write_text(BROWSER_HEAD_SOURCE, encoding="utf-8")
            _git(root, "add", "web/tests/four-player.spec.ts")
            _git(root, "commit", "-qm", "head")
            head = _git(root, "rev-parse", "HEAD")
            runs = {
                "workflow_runs": [
                    {
                        "id": 42,
                        "path": ".github/workflows/main-broad.yml",
                        "event": "push",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_branch": "main",
                        "head_sha": base,
                    }
                ]
            }
            jobs = {
                "jobs": [
                    {
                        "id": 800,
                        "name": "Main / Broad / Browser / soak",
                        "conclusion": "failure",
                    },
                    {
                        "id": 801,
                        "name": "Main / Broad regression",
                        "conclusion": "failure",
                    },
                ]
            }
            report = {
                "config": {"metadata": {"ci": {"commitHash": base}}},
                "suites": [
                    {
                        "file": "four-player.spec.ts",
                        "specs": [
                            {
                                "file": "four-player.spec.ts",
                                "title": BROWSER_TITLE,
                                "ok": False,
                                "tests": [
                                    {
                                        "status": "unexpected",
                                        "results": [
                                            {
                                                "error": {
                                                    "location": {
                                                        "file": (
                                                            "/repo/web/tests/"
                                                            "four-player.spec.ts"
                                                        )
                                                    }
                                                },
                                                "errors": [],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "stats": {"unexpected": 1},
            }
            with (
                patch(
                    "scripts.main_health._json_documents",
                    return_value=((root / "main-broad-soak.json", report),),
                ),
                patch(
                    "scripts.main_health._generated_outputs_for_recovery",
                    return_value=((), ()),
                ),
            ):
                plan = verify_recovery_route(
                    runs=runs,
                    jobs=jobs,
                    results_dir=root,
                    base_sha=base,
                    head_sha=head,
                    root=root,
                )
            self.assertEqual(2, plan["schema_version"])
            self.assertEqual([], plan["test_modules"])
            self.assertEqual(
                ["web/tests/four-player.spec.ts"],
                plan["browser_test_files"],
            )
            self.assertEqual(1, len(plan["browser_focus_patterns"]))
            self.assertEqual("browser", plan["failed_jobs"][0]["kind"])


if __name__ == "__main__":
    unittest.main()
