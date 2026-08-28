from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from scripts.certification_receipt import (
    CertificationReceipt,
    CertificationReceiptError,
    GOVERNANCE_REQUIRED_CHECK_SUITE,
    RECEIPT_FILENAME,
    REQUIRED_CHECK_SUITE,
    build_reused_receipt,
    canonical_check_suite,
    find_previous_pr_certification,
    main as certification_receipt_main,
    receipt_from_archive,
    select_merged_pull_request,
    select_receipt_artifact,
    successful_pr_runs,
    validate_receipt,
    verify_main_certification,
    wait_for_previous_pr_certification,
)
from scripts.source_tree_fingerprint import (
    tracked_ref_source_fingerprint,
    tracked_worktree_source_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]


def required_needs() -> dict[str, dict[str, str]]:
    return {
        name: {"result": "success"}
        for name in REQUIRED_CHECK_SUITE
    }


def receipt() -> CertificationReceipt:
    return CertificationReceipt(
        repository="MoellerJDev/quorune",
        pull_request=132,
        exact_head_sha="a" * 40,
        workflow_run_id=12345,
        check_suite=tuple(canonical_check_suite(required_needs()).items()),
        source_tree_fingerprint="b" * 64,
        generated_outputs_fingerprint="c" * 64,
    )


class CertificationReceiptTests(unittest.TestCase):
    def test_squash_equivalent_source_tree_remains_certified(self):
        value = receipt()
        validate_receipt(
            value,
            repository=value.repository,
            pull_request=value.pull_request,
            exact_head_sha=value.exact_head_sha,
            workflow_run_id=value.workflow_run_id,
            evaluated_source_tree_fingerprint=value.source_tree_fingerprint,
            evaluated_generated_outputs_fingerprint=(
                value.generated_outputs_fingerprint
            ),
        )

    def test_complete_profile_is_explicit_and_survives_metadata_reuse(self):
        complete = replace(receipt(), certification_profile="complete")
        self.assertEqual("complete", complete.to_dict()["certification_profile"])
        self.assertEqual(
            "complete",
            build_reused_receipt(complete, workflow_run_id=23456).certification_profile,
        )

    def test_metadata_only_publication_preserves_original_evidence_run(self):
        prior = receipt()
        first_reuse = build_reused_receipt(prior, workflow_run_id=23456)
        second_reuse = build_reused_receipt(
            first_reuse,
            workflow_run_id=34567,
        )
        self.assertEqual("reused", first_reuse.certification_mode)
        self.assertEqual(23456, first_reuse.workflow_run_id)
        self.assertEqual(12345, first_reuse.evidence_workflow_run_id)
        self.assertEqual(12345, second_reuse.evidence_workflow_run_id)
        self.assertEqual(prior.check_suite, second_reuse.check_suite)
        with self.assertRaisesRegex(
            CertificationReceiptError,
            "new publication workflow",
        ):
            build_reused_receipt(prior, workflow_run_id=12345)

    def test_materially_changed_source_tree_is_not_certified(self):
        value = receipt()
        with self.assertRaisesRegex(CertificationReceiptError, "not equivalent"):
            validate_receipt(
                value,
                repository=value.repository,
                pull_request=value.pull_request,
                exact_head_sha=value.exact_head_sha,
                workflow_run_id=value.workflow_run_id,
                evaluated_source_tree_fingerprint="c" * 64,
                evaluated_generated_outputs_fingerprint=(
                    value.generated_outputs_fingerprint
                ),
            )
        with self.assertRaisesRegex(CertificationReceiptError, "generated outputs"):
            validate_receipt(
                value,
                repository=value.repository,
                pull_request=value.pull_request,
                exact_head_sha=value.exact_head_sha,
                workflow_run_id=value.workflow_run_id,
                evaluated_source_tree_fingerprint=value.source_tree_fingerprint,
                evaluated_generated_outputs_fingerprint="d" * 64,
            )

    def test_stale_or_mismatched_receipt_fails_closed(self):
        value = receipt()
        for field, replacement in (
            ("repository", "other/repository"),
            ("pull_request", 999),
            ("exact_head_sha", "c" * 40),
            ("workflow_run_id", 999),
        ):
            arguments = {
                "repository": value.repository,
                "pull_request": value.pull_request,
                "exact_head_sha": value.exact_head_sha,
                "workflow_run_id": value.workflow_run_id,
                "evaluated_source_tree_fingerprint": value.source_tree_fingerprint,
                "evaluated_generated_outputs_fingerprint": (
                    value.generated_outputs_fingerprint
                ),
            }
            arguments[field] = replacement
            with self.subTest(field=field):
                with self.assertRaisesRegex(CertificationReceiptError, field):
                    validate_receipt(value, **arguments)

        malformed = value.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(CertificationReceiptError, "unknown"):
            CertificationReceipt.from_dict(malformed)

    def test_required_ci_check_suite_cannot_be_weakened(self):
        for missing in sorted(REQUIRED_CHECK_SUITE):
            needs = required_needs()
            needs.pop(missing)
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(CertificationReceiptError, "every required"):
                    canonical_check_suite(needs)

        needs = required_needs()
        needs["generated"]["result"] = "skipped"
        with self.assertRaisesRegex(CertificationReceiptError, "did not succeed"):
            canonical_check_suite(needs)

        governance = {
            name: {"result": "success"}
            for name in GOVERNANCE_REQUIRED_CHECK_SUITE
        }
        self.assertEqual(
            {"generated": "success", "plan": "success"},
            canonical_check_suite(
                governance,
                GOVERNANCE_REQUIRED_CHECK_SUITE,
            ),
        )
        governance["generated"]["result"] = "skipped"
        with self.assertRaisesRegex(CertificationReceiptError, "did not succeed"):
            canonical_check_suite(governance, GOVERNANCE_REQUIRED_CHECK_SUITE)

    def test_current_clean_tree_matches_its_committed_head_without_self_reference(self):
        self.assertEqual(
            tracked_ref_source_fingerprint(ROOT, "HEAD"),
            tracked_worktree_source_fingerprint(ROOT),
        )

    def test_github_merge_run_and_artifact_selection_is_strict(self):
        pull = select_merged_pull_request(
            [
                {
                    "number": 132,
                    "state": "closed",
                    "merged_at": "2026-08-07T00:00:00Z",
                    "merge_commit_sha": "d" * 40,
                    "head": {"sha": "a" * 40},
                }
            ],
            merge_sha="d" * 40,
        )
        self.assertEqual(132, pull["number"])
        runs = successful_pr_runs(
            {
                "workflow_runs": [
                    {
                        "id": 12345,
                        "event": "pull_request",
                        "head_sha": "a" * 40,
                        "conclusion": "success",
                        "name": "PR",
                        "path": ".github/workflows/ci.yml",
                    },
                    {
                        "id": 99999,
                        "event": "push",
                        "head_sha": "a" * 40,
                        "conclusion": "success",
                        "name": "PR",
                        "path": ".github/workflows/ci.yml",
                    },
                ]
            },
            exact_head_sha="a" * 40,
        )
        self.assertEqual([12345], [row["id"] for row in runs])
        artifact = select_receipt_artifact(
            {
                "artifacts": [
                    {
                        "name": "exact-head-certification-12345",
                        "expired": False,
                        "archive_download_url": "https://example.invalid/receipt",
                    }
                ]
            },
            workflow_run_id=12345,
        )
        self.assertEqual(
            "https://example.invalid/receipt",
            artifact["archive_download_url"],
        )

    def test_merged_pull_selection_deduplicates_sources_and_fails_closed(self):
        merged = {
            "number": 132,
            "state": "closed",
            "merged_at": "2026-08-07T00:00:00Z",
            "merge_commit_sha": "d" * 40,
            "head": {"sha": "a" * 40},
        }
        selected = select_merged_pull_request(
            [merged, dict(merged)],
            merge_sha="d" * 40,
        )
        self.assertEqual(132, selected["number"])
        with self.assertRaisesRegex(
            CertificationReceiptError,
            "exactly one",
        ):
            select_merged_pull_request([], merge_sha="d" * 40)
        with self.assertRaisesRegex(
            CertificationReceiptError,
            "exactly one",
        ):
            select_merged_pull_request(
                [merged, {**merged, "number": 133}],
                merge_sha="d" * 40,
            )

    def test_verify_main_falls_back_to_recent_squash_merge_payload(self):
        expected = receipt()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr(
                RECEIPT_FILENAME,
                json.dumps(expected.to_dict(), sort_keys=True),
            )
        recent_pull = {
            "number": expected.pull_request,
            "state": "closed",
            "merged_at": "2026-08-07T00:00:00Z",
            "merge_commit_sha": "d" * 40,
            "head": {"sha": expected.exact_head_sha},
        }
        responses = iter(
            (
                [],
                [recent_pull],
                {
                    "workflow_runs": [
                        {
                            "id": expected.workflow_run_id,
                            "event": "pull_request",
                            "head_sha": expected.exact_head_sha,
                            "name": "PR",
                            "path": ".github/workflows/ci.yml",
                        }
                    ]
                },
                {
                    "artifacts": [
                        {
                            "name": (
                                "exact-head-certification-"
                                f"{expected.workflow_run_id}"
                            ),
                            "expired": False,
                            "archive_download_url": (
                                "https://example.invalid/receipt"
                            ),
                        }
                    ]
                },
            )
        )
        with (
            patch(
                "scripts.certification_receipt._github_json",
                side_effect=lambda *_args: next(responses),
            ),
            patch(
                "scripts.certification_receipt._github_request",
                return_value=archive.getvalue(),
            ),
            patch(
                "scripts.certification_receipt."
                "tracked_worktree_source_fingerprint",
                return_value=expected.source_tree_fingerprint,
            ),
            patch(
                "scripts.certification_receipt."
                "generated_outputs_fingerprint",
                return_value=expected.generated_outputs_fingerprint,
            ),
        ):
            actual = verify_main_certification(
                repository=expected.repository,
                merge_sha="d" * 40,
                token="test-token",
            )
        self.assertEqual(expected, actual)

    def test_artifact_archive_requires_one_canonical_receipt(self):
        expected = receipt()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(
                RECEIPT_FILENAME,
                json.dumps(expected.to_dict(), sort_keys=True),
            )
        self.assertEqual(expected, receipt_from_archive(stream.getvalue()))

        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(RECEIPT_FILENAME, "{}")
            archive.writestr("extra.txt", "unexpected")
        with self.assertRaisesRegex(CertificationReceiptError, "only"):
            receipt_from_archive(stream.getvalue())

    def test_prior_exact_head_lookup_validates_receipt_before_reuse(self):
        prior = receipt()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr(
                RECEIPT_FILENAME,
                json.dumps(prior.to_dict(), sort_keys=True),
            )
        responses = iter(
            (
                {
                    "workflow_runs": [
                        {
                            "id": 12345,
                            "event": "pull_request",
                            "head_sha": "a" * 40,
                            "name": "PR",
                            "path": ".github/workflows/ci.yml",
                        },
                        {
                            "id": 23456,
                            "event": "pull_request",
                            "head_sha": "a" * 40,
                            "name": "PR",
                            "path": ".github/workflows/ci.yml",
                        },
                    ]
                },
                {
                    "artifacts": [
                        {
                            "name": "exact-head-certification-12345",
                            "expired": False,
                            "archive_download_url": "https://example.invalid/receipt",
                        }
                    ]
                },
            )
        )
        with (
            patch(
                "scripts.certification_receipt._github_json",
                side_effect=lambda *_args: next(responses),
            ),
            patch(
                "scripts.certification_receipt._github_request",
                return_value=archive.getvalue(),
            ),
            patch(
                "scripts.certification_receipt."
                "tracked_worktree_source_fingerprint",
                return_value=prior.source_tree_fingerprint,
            ),
            patch(
                "scripts.certification_receipt."
                "generated_outputs_fingerprint",
                return_value=prior.generated_outputs_fingerprint,
            ),
        ):
            found = find_previous_pr_certification(
                repository=prior.repository,
                pull_request=prior.pull_request,
                exact_head_sha=prior.exact_head_sha,
                current_workflow_run_id=23456,
                token="test-token",
            )
        self.assertEqual(prior, found)

    def test_metadata_edit_waits_for_active_unchanged_head_certification(self):
        prior = receipt()
        active_runs = {
            "workflow_runs": [
                {
                    "id": prior.workflow_run_id,
                    "event": "pull_request",
                    "head_sha": prior.exact_head_sha,
                    "name": "PR",
                    "path": ".github/workflows/ci.yml",
                    "status": "in_progress",
                }
            ]
        }

        def github_json(url, _token):
            if "/jobs?filter=latest&per_page=100" in url:
                return {
                    "total_count": 1,
                    "jobs": [{"status": "in_progress", "conclusion": None}],
                }
            return active_runs

        with (
            patch(
                "scripts.certification_receipt."
                "find_previous_pr_certification",
                side_effect=(
                    CertificationReceiptError("receipt is pending"),
                    prior,
                ),
            ),
            patch(
                "scripts.certification_receipt._github_json",
                side_effect=github_json,
            ),
            patch("scripts.certification_receipt.time.sleep") as sleep,
        ):
            actual = wait_for_previous_pr_certification(
                repository=prior.repository,
                pull_request=prior.pull_request,
                exact_head_sha=prior.exact_head_sha,
                current_workflow_run_id=23456,
                token="test-token",
                wait_seconds=30,
            )

        self.assertEqual(prior, actual)
        sleep.assert_called_once_with(15.0)

    def test_metadata_edit_ignores_active_run_with_failed_job(self):
        prior = receipt()
        active_runs = {
            "workflow_runs": [
                {
                    "id": prior.workflow_run_id,
                    "event": "pull_request",
                    "head_sha": prior.exact_head_sha,
                    "name": "PR",
                    "path": ".github/workflows/ci.yml",
                    "status": "in_progress",
                }
            ]
        }

        def github_json(url, _token):
            if "/jobs?filter=latest&per_page=100" in url:
                return {
                    "total_count": 2,
                    "jobs": [
                        {"status": "completed", "conclusion": "failure"},
                        {"status": "in_progress", "conclusion": None},
                    ],
                }
            return active_runs

        with (
            patch(
                "scripts.certification_receipt."
                "find_previous_pr_certification",
                side_effect=CertificationReceiptError("receipt is pending"),
            ),
            patch(
                "scripts.certification_receipt._github_json",
                side_effect=github_json,
            ),
            patch("scripts.certification_receipt.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                CertificationReceiptError, "receipt is pending"
            ):
                wait_for_previous_pr_certification(
                    repository=prior.repository,
                    pull_request=prior.pull_request,
                    exact_head_sha=prior.exact_head_sha,
                    current_workflow_run_id=23456,
                    token="test-token",
                    wait_seconds=30,
                )

        sleep.assert_not_called()

    def test_metadata_edit_does_not_wait_for_newer_unchanged_head_run(self):
        prior = receipt()
        newer_runs = {
            "workflow_runs": [
                {
                    "id": 34567,
                    "event": "pull_request",
                    "head_sha": prior.exact_head_sha,
                    "name": "PR",
                    "path": ".github/workflows/ci.yml",
                    "status": "in_progress",
                }
            ]
        }

        with (
            patch(
                "scripts.certification_receipt."
                "find_previous_pr_certification",
                side_effect=CertificationReceiptError("receipt is pending"),
            ),
            patch(
                "scripts.certification_receipt._github_json",
                return_value=newer_runs,
            ),
            patch("scripts.certification_receipt.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                CertificationReceiptError, "receipt is pending"
            ):
                wait_for_previous_pr_certification(
                    repository=prior.repository,
                    pull_request=prior.pull_request,
                    exact_head_sha=prior.exact_head_sha,
                    current_workflow_run_id=23456,
                    token="test-token",
                    wait_seconds=30,
                )

        sleep.assert_not_called()

    def test_metadata_edit_ignores_stale_zero_job_rerun(self):
        prior = receipt()
        queued_runs = {
            "workflow_runs": [
                {
                    "id": prior.workflow_run_id,
                    "event": "pull_request",
                    "head_sha": prior.exact_head_sha,
                    "name": "PR",
                    "path": ".github/workflows/ci.yml",
                    "status": "queued",
                    "updated_at": "1970-01-01T00:00:00Z",
                }
            ]
        }

        def github_json(url, _token):
            if url.endswith("/jobs?per_page=1"):
                return {"total_count": 0, "jobs": []}
            return queued_runs

        with (
            patch(
                "scripts.certification_receipt."
                "find_previous_pr_certification",
                side_effect=CertificationReceiptError("receipt is pending"),
            ),
            patch(
                "scripts.certification_receipt._github_json",
                side_effect=github_json,
            ),
            patch("scripts.certification_receipt.time.time", return_value=601.0),
            patch("scripts.certification_receipt.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                CertificationReceiptError, "receipt is pending"
            ):
                wait_for_previous_pr_certification(
                    repository=prior.repository,
                    pull_request=prior.pull_request,
                    exact_head_sha=prior.exact_head_sha,
                    current_workflow_run_id=23456,
                    token="test-token",
                    wait_seconds=30,
                )

        sleep.assert_not_called()

    def test_metadata_edit_waits_for_recent_zero_job_rerun(self):
        prior = receipt()
        queued_runs = {
            "workflow_runs": [
                {
                    "id": prior.workflow_run_id,
                    "event": "pull_request",
                    "head_sha": prior.exact_head_sha,
                    "name": "PR",
                    "path": ".github/workflows/ci.yml",
                    "status": "queued",
                    "updated_at": "1970-01-01T00:05:01Z",
                }
            ]
        }

        def github_json(url, _token):
            if url.endswith("/jobs?per_page=1"):
                return {"total_count": 0, "jobs": []}
            return queued_runs

        with (
            patch(
                "scripts.certification_receipt."
                "find_previous_pr_certification",
                side_effect=(
                    CertificationReceiptError("receipt is pending"),
                    prior,
                ),
            ),
            patch(
                "scripts.certification_receipt._github_json",
                side_effect=github_json,
            ),
            patch("scripts.certification_receipt.time.time", return_value=600.0),
            patch("scripts.certification_receipt.time.sleep") as sleep,
        ):
            actual = wait_for_previous_pr_certification(
                repository=prior.repository,
                pull_request=prior.pull_request,
                exact_head_sha=prior.exact_head_sha,
                current_workflow_run_id=23456,
                token="test-token",
                wait_seconds=30,
            )

        self.assertEqual(prior, actual)
        sleep.assert_called_once_with(15.0)

    def test_source_workflow_preserves_required_gates_and_publishes_receipt(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        certification = workflow.split("  certification:\n", 1)[1].split(
            "\n  metrics:\n", 1
        )[0]
        self.assertIn(
            "needs: [plan, python, generated, package, windows_certification, browser]",
            certification,
        )
        self.assertIn("python scripts/verify_ci_needs.py", certification)
        self.assertIn("certification_receipt.py create", certification)
        self.assertIn("--certification-profile", certification)
        checkout = certification.split("actions/checkout@v4", 1)[1].split(
            "Require every PR gate", 1
        )[0]
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha }}",
            checkout,
        )
        self.assertIn("fetch-depth: 1", checkout)
        self.assertNotIn("certification_receipt.py reuse-pr", certification)
        self.assertNotIn("needs.plan.outputs.reuse_certification", certification)
        self.assertIn("actions/upload-artifact@v4", certification)

    def test_invalid_main_receipt_falls_closed_to_a_fresh_broad_matrix(self):
        with TemporaryDirectory() as raw:
            output = Path(raw) / "github-output.txt"
            with (
                patch.dict("os.environ", {"GH_TOKEN": "test-token"}, clear=False),
                patch(
                    "scripts.certification_receipt.verify_main_certification",
                    side_effect=CertificationReceiptError("stale receipt"),
                ),
            ):
                status = certification_receipt_main(
                    [
                        "can-reuse-main-broad",
                        "--repository",
                        "MoellerJDev/quorune",
                        "--merge-sha",
                        "d" * 40,
                        "--github-output",
                        str(output),
                    ]
                )
            self.assertEqual(0, status)
            self.assertEqual(
                "reuse_main_broad=false\n", output.read_text(encoding="utf-8")
            )


if __name__ == "__main__":
    unittest.main()
