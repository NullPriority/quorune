from __future__ import annotations

import json
from collections import Counter
import os
import tempfile
import unittest
from pathlib import Path
import sys

from quorune import CardDatabase
from scripts.build_test_database import (
    build_fixture_database,
    compact_ci_fixture_paths,
    validate_compact_ci_consumers,
)
from scripts.local_merge_gate import (
    DEFAULT_FOCUSED_TESTS,
    PRIVACY_TESTS,
    build_steps,
    gate_environment,
)


class LocalMergeGateTests(unittest.TestCase):
    def test_compact_card_fixtures_compose_without_external_data(self):
        root = Path(__file__).resolve().parents[1]
        fixtures = list(compact_ci_fixture_paths())
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "test-ci.sqlite3"
            result = build_fixture_database(fixtures, database)
            with CardDatabase(database) as card_db:
                self.assertEqual("Zimone and Dina", card_db.lookup("Zimone and Dina").name)
                self.assertEqual(
                    "Yargle and Multani",
                    card_db.lookup("Yargle and Multani").name,
                )

        self.assertEqual([str(fixture) for fixture in fixtures], result["fixtures"])
        fixture_data = [
            json.loads(fixture.read_text(encoding="utf-8"))
            for fixture in fixtures
        ]
        # Overlap is one published snapshot, while repeated rows within any
        # individual snapshot retain their original maximum multiplicity.
        occurrences = [Counter(
            (row['oracle_id'], row['published_at'], row['source'], row['comment'])
            for row in value.get('rulings', ())
        ) for value in fixture_data]
        identities = set().union(*(set(counts) for counts in occurrences))
        expected = sum(max(counts[identity] for counts in occurrences) for identity in identities)
        self.assertEqual(expected, result["rulings"])

    def test_compact_ci_manifest_is_the_only_consumer_fixture_source(self):
        result = validate_compact_ci_consumers()

        self.assertTrue(result["ok"])
        self.assertGreater(result["invocations"], 0)
        self.assertEqual(
            [
                path.relative_to(
                    Path(__file__).resolve().parents[1]
                ).as_posix()
                for path in compact_ci_fixture_paths()
            ],
            result["fixtures"],
        )

    def test_compact_ci_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixtures" / "cards.json"
            fixture.parent.mkdir()
            fixture.write_text("{}\n", encoding="utf-8")
            manifest = root / "manifest.json"

            cases = (
                (["fixtures/cards.json", "fixtures/cards.json"], "duplicates"),
                (["../outside.json"], "canonical repository-relative"),
                (["fixtures/missing.json"], "does not exist"),
            )
            for entries, message in cases:
                with self.subTest(entries=entries):
                    manifest.write_text(
                        json.dumps(
                            {
                                "schema_version": 2,
                                "fixture_kind": "compact_ci_card_database_inputs",
                                "fixtures": entries,
                                "dynamic_requirements": [],
                                "full_database_only": [],
                            }
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        compact_ci_fixture_paths(manifest, root=root)

    def test_new_workflow_cannot_add_an_independent_fixture_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "tests" / "fixtures" / "cards.json"
            fixture.parent.mkdir(parents=True)
            fixture.write_text("{}\n", encoding="utf-8")
            (fixture.parent / "compact-ci-fixtures.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "fixture_kind": "compact_ci_card_database_inputs",
                        "fixtures": ["tests/fixtures/cards.json"],
                        "dynamic_requirements": [],
                        "full_database_only": [],
                    }
                ),
                encoding="utf-8",
            )
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "new.yml").write_text(
                "python scripts/build_test_database.py build "
                "--fixture tests/fixtures/cards.json\n",
                encoding="utf-8",
            )
            scripts = root / "scripts"
            scripts.mkdir()
            for name in ("local_merge_gate.py", "quick_gate.py"):
                (scripts / name).write_text(
                    '"scripts/build_test_database.py", "build-ci"\n',
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(
                ValueError,
                "independent fixture list",
            ):
                validate_compact_ci_consumers(root=root)

    def test_gate_orchestrates_every_required_existing_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            steps = build_steps(
                "python-under-test",
                database=output / "test-ci.sqlite3",
                output=output,
                focused_tests=DEFAULT_FOCUSED_TESTS,
            )

        by_name = {step.name: step.command for step in steps}
        self.assertEqual(
            {
                "generated_capability_evidence_freshness",
                "generated_card_unlock_frontier_freshness",
                "generated_reusable_piece_freshness",
                "generated_ci_escape_report_freshness",
                "python_runtime_policy",
                "generated_rules_scheduler_freshness",
                "module_classification_freshness",
                "continuous_effect_work_budget",
                "generated_platform_freshness",
                "compile",
                "compact_ci_dependency_closure",
                "generated_compact_ci_dependency_freshness",
                "build_test_database",
                "full_deterministic_suite",
                "rules_corpus_verify",
                "architecture_policy",
                "documentation_policy",
                "focused_regressions",
                "four_player_natural_winner",
                "projection_and_privacy",
                "protocol_demo",
                "dependency_check",
                "repository_security_validation",
                "wheel_build",
                "wheel_clean_install",
                "browser_dependencies",
                "generated_protocol_types",
                "generated_protocol_freshness",
                "browser_production_build",
                "browser_four_context_e2e",
            },
            set(by_name),
        )
        self.assertIn("discover", by_name["full_deterministic_suite"])
        self.assertEqual(
            ("python-under-test", "scripts/validate_python_runtime.py"),
            by_name["python_runtime_policy"],
        )
        self.assertIn("build-ci", by_name["build_test_database"])
        self.assertNotIn("--fixture", by_name["build_test_database"])
        self.assertIn(
            "tests.test_seed_20260730_regression",
            by_name["focused_regressions"],
        )
        self.assertIn(
            "tests.test_command_zone_rules",
            by_name["focused_regressions"],
        )
        self.assertIn(
            "tests.test_deterministic_full_game",
            by_name["four_player_natural_winner"],
        )
        for test_module in PRIVACY_TESTS:
            self.assertIn(
                test_module,
                by_name["projection_and_privacy"],
            )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/update_capability_evidence.py",
                "--check",
            ),
            by_name["generated_capability_evidence_freshness"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/update_card_unlock_frontier.py",
                "--check",
            ),
            by_name["generated_card_unlock_frontier_freshness"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/update_reusable_piece_matrix.py",
                "--check",
            ),
            by_name["generated_reusable_piece_freshness"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/update_ci_escape_report.py",
                "--check",
            ),
            by_name["generated_ci_escape_report_freshness"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/update_rules_scheduler.py",
                "--check",
            ),
            by_name["generated_rules_scheduler_freshness"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/update_module_classifications.py",
                "--check",
            ),
            by_name["module_classification_freshness"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/benchmark_continuous_effects.py",
                "--check",
            ),
            by_name["continuous_effect_work_budget"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/validate_architecture.py",
                "--check",
            ),
            by_name["architecture_policy"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/validate_documentation.py",
                "--check",
            ),
            by_name["documentation_policy"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/validate_repository.py",
            ),
            by_name["repository_security_validation"],
        )
        self.assertEqual(
            (
                "python-under-test",
                "scripts/update_platform_status.py",
                "--check",
            ),
            by_name["generated_platform_freshness"],
        )
        self.assertEqual(
            ("npm", "run", "e2e", "--prefix", "web"),
            by_name["browser_four_context_e2e"],
        )

    def test_additional_focused_tests_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            steps = build_steps(
                "python-under-test",
                database=output / "test-ci.sqlite3",
                output=output,
                focused_tests=(
                    *DEFAULT_FOCUSED_TESTS,
                    "tests.test_stack_rules",
                ),
            )

        focused = next(
            step.command
            for step in steps
            if step.name == "focused_regressions"
        )
        self.assertEqual(
            "tests.test_stack_rules",
            focused[-1],
        )

    def test_gate_environment_supports_discovery_style_common_imports(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "test-ci.sqlite3"
            environment = gate_environment(database)

        self.assertEqual(
            str(database),
            environment["MTG_CARD_DB"],
        )
        self.assertEqual(
            str(Path(sys.executable).resolve()),
            environment["MTG_PYTHON_EXECUTABLE"],
        )
        self.assertIn(
            str(Path(__file__).resolve().parent),
            environment["PYTHONPATH"].split(os.pathsep),
        )


if __name__ == "__main__":
    unittest.main()
