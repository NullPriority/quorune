from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from scripts.quick_gate import build_plan


class QuickGatePlanTests(unittest.TestCase):
    def test_pre_corpus_text_encoding_uses_bounded_command_and_exact_input(self):
        paths=('tests/test_quick_gate.py',)*1200+('docs/index.md',)
        plan=build_plan(paths,phase='pre-corpus')
        step=next(step for step in plan['steps'] if step.name=='explicit-text-encoding')
        self.assertLess(len(subprocess.list2cmdline(step.command)),1024)
        self.assertIn('--changed-files-stdin',step.command)
        self.assertEqual(list(paths[:-1]),json.loads(step.input_text))

    def test_large_explicit_file_list_reaches_real_encoding_validation_without_argv_growth(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            good=Path(directory)/'valid-source-é.py';bad=Path(directory)/'invalid-source.py'
            good.write_text("from pathlib import Path\nPath('x').read_text(encoding='utf-8')\n",encoding='utf-8')
            bad.write_text("from pathlib import Path\nPath('x').read_text()\n",encoding='utf-8')
            paths=[str(good)]*1200+[str(bad)]
            self.assertGreater(len(subprocess.list2cmdline([argument for path in paths for argument in ('--changed-file',path)])),32767)
            command=[sys.executable,'scripts/quick_gate.py','--validate-text-io','--changed-files-stdin']
            result=subprocess.run(command,cwd=root,input=json.dumps(paths),text=True,encoding='utf-8',capture_output=True)
            self.assertEqual(1,result.returncode,result.stderr)
            value=json.loads(result.stdout);self.assertEqual(1201,value['checked_python_files'])
            self.assertEqual([str(bad)],[row['path'] for row in value['violations']])
            empty=subprocess.run(command,cwd=root,input='[]',text=True,encoding='utf-8',capture_output=True)
            self.assertEqual(0,empty.returncode,empty.stderr);self.assertEqual(0,json.loads(empty.stdout)['checked_python_files'])

    def test_explicit_file_list_rejects_malformed_input_and_ambiguous_selection(self):
        root=Path(__file__).resolve().parents[1]
        command=[sys.executable,'scripts/quick_gate.py','--validate-text-io','--changed-files-stdin']
        for value in ('{}','true','[null]','[""]','not JSON'):
            with self.subTest(value=value):
                result=subprocess.run(command,cwd=root,input=value,text=True,encoding='utf-8',capture_output=True)
                self.assertEqual(2,result.returncode)
        result=subprocess.run([*command,'--changed-file','tests/test_quick_gate.py'],cwd=root,input='[]',text=True,encoding='utf-8',capture_output=True)
        self.assertEqual(2,result.returncode)

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

    def test_typed_query_change_selects_legacy_owner_overlap(self):
        plan = build_plan(
            ("quorune/compiler/query_characteristic_templates.py",)
        )

        self.assertIn(
            "test_fixed_public_state_characteristics",
            plan["test_modules"],
        )

        public_state_plan = build_plan(
            ("quorune/compiler/public_state_queries.py",)
        )
        self.assertIn(
            "test_indestructible_rules",
            public_state_plan["test_modules"],
        )
        self.assertIn(
            "test_rules_scheduler",
            public_state_plan["test_modules"],
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
        self.assertIn("rules-selection-policy", names)
        policy_step = next(
            step for step in plan["steps"] if step.name == "reusable-piece-policy"
        )
        self.assertEqual(
            ("scripts/update_reusable_piece_matrix.py", "--validate-policy"),
            policy_step.command[-2:],
        )
        self.assertIn("change-impact-contract", names)
        self.assertIn("mutation-patch-target-integrity", names)
        self.assertNotIn("generated-finalization", names)
        self.assertNotIn("compact-ci-dependencies", names)
        self.assertNotIn("build-test-database", names)
        self.assertNotIn("affected-tests", names)
        self.assertEqual((), plan["test_modules"])
        self.assertTrue(plan["deferred_test_modules"])

    def test_pre_corpus_search_change_checks_the_saga_residual_carrier(self):
        plan = build_plan(
            ("quorune/compiler/library_search_templates.py",),
            phase="pre-corpus",
        )
        step = next(
            step for step in plan["steps"]
            if step.name == "search-saga-residual-carrier-contract"
        )
        self.assertEqual(
            "test_counted_search_promotion_preserves_saga_reanimation_boundary",
            step.command[-1],
        )
        self.assertEqual((), plan["test_modules"])

    def test_pre_corpus_target_carrier_sentinel_is_owned_and_bounded(self):
        plan = build_plan(
            ("quorune/compiler/tap_state_event_bindings.py",),
            phase="pre-corpus",
        )
        steps = {step.name: step.command for step in plan["steps"]}
        self.assertIn("target-corpus-carrier-contract", steps)
        self.assertEqual(
            "test_tap_state_target_body_is_accepted_by_corpus_assurance",
            steps["target-corpus-carrier-contract"][-1],
        )
        self.assertNotIn("affected-tests", steps)

    def test_pre_corpus_quantity_change_selects_exact_adjacent_boundaries(self):
        plan = build_plan(
            ("quorune/compiler/collection_quantity_templates.py",),
            phase="pre-corpus",
        )
        boundaries = [step for step in plan["steps"]
                      if step.name.startswith("quantity-boundary-")]
        self.assertEqual(6, len(boundaries))
        self.assertTrue(all("-k" in step.command for step in boundaries))
        self.assertEqual((), plan["test_modules"])

    def test_pre_corpus_plan_runs_affected_manual_performance_contract(self):
        plan = build_plan(
            ("quorune/card_programs/runtime.py",),
            phase="pre-corpus",
            base_ref="origin/main",
        )
        steps = {step.name: step.command for step in plan["steps"]}

        self.assertEqual(
            (
                str(Path(sys.executable).resolve()),
                "scripts/benchmark_continuous_effects.py",
                "--check",
            ),
            steps["continuous-effect-performance"],
        )

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
