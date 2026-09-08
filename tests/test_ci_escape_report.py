from __future__ import annotations

import json
import unittest

from tests.common import ROOT
from scripts.update_ci_escape_report import build_report, markdown


def source() -> dict:
    return {
        "schema_version": 2,
        "observed_at": "2026-08-04T00:00:00Z",
        "measurement_window": {
            "started_at": "2026-08-01T00:00:00Z",
            "ended_at": "2026-08-04T00:00:00Z",
            "population": "tracked recent pull requests",
            "sample_size": 1,
        },
        "repository": "example/repository",
        "escapes": [
            {
                "id": "escape-1",
                "run_id": 1,
                "head_sha": "a" * 40,
                "category": "source_correctness",
                "deterministic": True,
                "summary": "A deterministic failure escaped.",
                "failed_surfaces": ["rules"],
                "regressions": ["test_rules"],
                "impact_edge_status": "added",
                "resolution": "The missing edge was added.",
            },
            {
                "id": "escape-2",
                "run_id": 2,
                "head_sha": "b" * 40,
                "category": "source_correctness",
                "deterministic": True,
                "summary": "A second deterministic failure escaped.",
                "failed_surfaces": ["rules"],
                "regressions": ["test_rules_again"],
                "impact_edge_status": "pending",
                "resolution": "The edge remains pending.",
            },
        ],
        "recent_pull_requests": [
            {
                "number": 1,
                "head_sha": "c" * 40,
                "final_run_id": 3,
                "final_run_conclusion": "success",
                "workflow_runs_observed": 2,
                "push_count": None,
                "critical_path_seconds": 10.0,
                "slot_b_inactive_seconds": None,
            }
        ],
        "known_flaky_tests": [],
        "limitations": {
            "active_development_hours": "Unavailable.",
            "average_pushes_per_merged_pr": "Unavailable.",
            "first_eligible_head_certification": "Unavailable.",
            "slot_b_inactive_seconds": "Unavailable.",
        },
    }


class CiEscapeReportTests(unittest.TestCase):
    def test_report_counts_repeats_without_estimating_missing_values(self):
        report = build_report(source())
        summary = report["summary"]
        self.assertEqual(2, summary["deterministic_escape_count"])
        self.assertEqual(["source_correctness"], summary["repeated_failure_categories"])
        self.assertEqual(["escape-2"], summary["current_missing_impact_edges"])
        self.assertIsNone(summary["average_pushes_per_merged_pr"])
        self.assertIsNone(summary["average_slot_b_inactive_seconds"])
        self.assertIsNone(
            summary["first_eligible_head_certification_pass_rate"]
        )
        self.assertEqual(0, summary["first_eligible_head_certification_sample_size"])
        self.assertEqual(
            1.0,
            summary["eventual_final_head_certification_pass_rate"],
        )
        self.assertEqual(1, summary["eventual_final_head_certification_sample_size"])
        rendered = markdown(report)
        self.assertIn("Null measurements", rendered)
        self.assertIn("null (n=0)", rendered)
        self.assertIn("n=1", rendered)

    def test_unknown_fields_and_categories_fail_closed(self):
        value = source()
        value["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "unknown or missing"):
            build_report(value)
        value = source()
        value["escapes"][0]["category"] = "guess"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            build_report(value)
        value = source()
        value["measurement_window"]["sample_size"] = 2
        with self.assertRaisesRegex(ValueError, "must match"):
            build_report(value)

    def test_tracked_report_matches_authoritative_source(self):
        source_value = json.loads(
            (ROOT / "platform" / "ci-escape-source.json").read_text(
                encoding="utf-8"
            )
        )
        expected = build_report(source_value)
        tracked = json.loads(
            (ROOT / "coverage" / "ci-escape-report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(expected, tracked)


if __name__ == "__main__":
    unittest.main()
