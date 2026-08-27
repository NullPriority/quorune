from __future__ import annotations

import unittest

from scripts.quick_gate import build_plan


class QuickGatePlanTests(unittest.TestCase):
    def test_changed_test_and_subsystem_are_deduplicated(self):
        plan = build_plan(
            (
                "quorune/life_change.py",
                "tests/test_life_change.py",
            )
        )
        self.assertEqual(1, plan["test_modules"].count("test_life_change"))
        names = [step.name for step in plan["steps"]]
        self.assertIn("affected-tests", names)
        self.assertIn("compact-ci-dependencies", names)
        self.assertLess(
            names.index("compact-ci-dependencies"),
            names.index("affected-tests"),
        )
        self.assertIn("architecture", names)
        self.assertEqual(1, names.count("generated-finalization"))
        build = next(
            step.command
            for step in plan["steps"]
            if step.name == "build-test-database"
        )
        self.assertIn("build-ci", build)
        self.assertNotIn("--fixture", build)

    def test_docs_only_plan_runs_the_direct_policy_owner(self):
        plan = build_plan(("README.md",))
        names = [step.name for step in plan["steps"]]
        self.assertIn("build-test-database", names)
        self.assertIn("affected-tests", names)
        self.assertEqual(("test_documentation_policy",), plan["test_modules"])
        self.assertIn("generated-finalization", names)
        self.assertNotIn("documentation", names)

    def test_browser_plan_builds_without_running_e2e(self):
        plan = build_plan(("web/src/App.tsx",))
        names = [step.name for step in plan["steps"]]
        self.assertIn("browser-build", names)
        self.assertFalse(any("e2e" in name for name in names))

    def test_compiler_plan_checks_card_unlock_frontier(self):
        plan = build_plan(("quorune/compiler/oracle_parser.py",))
        names = [step.name for step in plan["steps"]]
        self.assertIn("generated-finalization", names)
        self.assertNotIn("card-unlock-frontier", names)
        self.assertNotIn("reusable-pieces", names)

    def test_pr322_escape_sources_select_focused_contract_modules(self):
        plan = build_plan(
            (
                "platform/rules-subsystems.json",
                "quorune/compiler/direct_target.py",
            )
        )

        self.assertIn("test_rules_scheduler", plan["test_modules"])
        self.assertIn(
            "test_targeted_return_to_hand_effect_clauses",
            plan["test_modules"],
        )

    def test_pre_corpus_plan_runs_identity_sentinels_without_generators(self):
        plan = build_plan(
            ("quorune/compiler/oracle_parser.py",),
            phase="pre-corpus",
            base_ref="origin/main",
        )
        names = [step.name for step in plan["steps"]]

        self.assertEqual("pre-corpus", plan["phase"])
        self.assertIn("generated-owner-plan", names)
        self.assertIn("compiler-identity", names)
        self.assertIn("architecture-policy", names)
        self.assertIn("capability-evidence-declarations", names)
        self.assertNotIn("generated-finalization", names)
        self.assertNotIn("compact-ci-dependencies", names)
        self.assertNotIn("build-test-database", names)
        self.assertNotIn("affected-tests", names)
        self.assertEqual((), plan["test_modules"])
        self.assertTrue(plan["deferred_test_modules"])

    def test_reusable_piece_change_checks_inventory(self):
        plan = build_plan(("quorune/reusable_pieces/generation.py",))
        names = [step.name for step in plan["steps"]]
        self.assertIn("generated-finalization", names)
        self.assertNotIn("reusable-pieces", names)

    def test_compact_card_dependency_sources_select_early_closure(self):
        for path in (
            "tests/fixtures/echo-rules-cards.json",
            "examples/mishra-eminent-one.txt",
            "platform/test-shards.json",
            "quorune/carddb.py",
        ):
            with self.subTest(path=path):
                plan = build_plan((path,))
                names = [step.name for step in plan["steps"]]
                self.assertIn("compact-ci-dependencies", names)
                if "affected-tests" in names:
                    self.assertLess(
                        names.index("compact-ci-dependencies"),
                        names.index("affected-tests"),
                    )


if __name__ == "__main__":
    unittest.main()
