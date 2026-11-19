from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.quick_gate import build_plan


class QuickGatePlanTests(unittest.TestCase):
    @patch('scripts.quick_gate.validate_partition')
    def test_artifact_guard_precedes_expensive_checks_and_both_push_paths(self, _partition):
        for phase in ('normal', 'pre-corpus'):
            with self.subTest(phase=phase):
                steps = build_plan(('docs/index.md',), phase=phase)['steps']
                guard, = [step for step in steps if step.name == 'repository-artifacts']
                self.assertEqual(('scripts/validate_repository.py', '--tracked-only'), guard.command[-2:])
                self.assertLess(steps.index(guard), next(i for i, step in enumerate(steps)
                    if step.name == 'generated-owner-plan'))
        root = Path(__file__).resolve().parents[1]
        hook = (root / '.githooks' / 'pre-push').read_text(encoding='utf-8')
        guard = hook.index('"$ROOT/scripts/validate_repository.py" --tracked-only')
        self.assertLess(guard, hook.index('validate-ci-dependencies'))
        self.assertLess(guard, hook.index('QUORUNE_CLOUD_SOURCE_CHECKPOINT_REASON'))
        self.assertLess(guard, hook.index('--verify-receipt'))

    @patch('scripts.quick_gate.validate_partition')
    def test_grammar_and_trust_changes_run_self_hosted_interaction_promotion_contract(self, _partition):
        for source in ('quorune/compiler/fixed_damage_roles.py', 'quorune/rules/temporary_keyword_removal.py',
                       'tests/high_risk_interaction_support.py', 'platform/reusable-piece-interaction-evidence.json'):
            for phase in ('normal', 'pre-corpus'):
                with self.subTest(source=source, phase=phase):
                    plan = build_plan((source,), phase=phase)
                    step, = [s for s in plan['steps'] if s.name == 'interaction-boundary-promotion-contract']
                    self.assertEqual(('test_high_risk_interaction_assurance.py', '-k', 'HighRiskPromotionSentinelTests'), step.command[-3:])
                    admission, = [s for s in plan['steps'] if s.name == 'compiler-promotion-admission-contract']
                    self.assertEqual(('fixed_subset_discard_draw','fixed_entwine','fixed_overload',
                                      'external_entry_tapped','external_entry_tapped','fixed_cleave',
                                      'source_tap_duration','activation_result_authority','activation_result_authority',
                                      'public_condition_self_replacements','public_condition_self_replacements',
                                      'optional_counter_payments','continuous_compiler_hardening','scoped_target_colors',
                                      'fixed_damage_effect_clauses','discard_results'),
                                     tuple(command.split('.')[1].removeprefix('test_') for command in admission.command[3:]))
                    self.assertTrue(all(command.startswith('tests.test_') for command in admission.command[3:]))
                    if phase == 'pre-corpus':
                        self.assertNotIn('affected-tests', [s.name for s in plan['steps']])
                        self.assertNotIn('build-test-database', [s.name for s in plan['steps']])
        unrelated = build_plan(('docs/index.md',), phase='pre-corpus')
        self.assertNotIn('interaction-boundary-promotion-contract', [s.name for s in unrelated['steps']])

    @patch('scripts.quick_gate.validate_partition')
    def test_player_sequence_changes_select_existing_compiler_consumers_before_census(self, _partition_validation):
        # This unit checks routing. Real partition validation remains covered by
        # the manifest owner and the normal/pre-corpus integration check below.
        sources=('quorune/compiler/target_player_sequences.py',
            'quorune/compiler/bound_effect_programs.py',
            'quorune/compiler/effect_template_composition.py')
        for source in sources:
            for phase in ('normal','pre-corpus'):
                with self.subTest(source=source,phase=phase):
                    plan=build_plan((source,),phase=phase)
                    step,=[row for row in plan['steps'] if row.name=='target-player-sequence-owner-contract']
                    self.assertEqual(('tests.test_bound_effect_programs.BoundEffectProgramCompilerTests',
                        'tests.test_fixed_affected_player_discards.FixedAffectedPlayerDiscardCompilerTests',
                        'tests.test_fixed_player_counter_placement_effects.FixedPlayerCounterPlacementCompilerTests'),step.command[-3:])
        unrelated=build_plan(('docs/index.md',),phase='pre-corpus')
        self.assertNotIn('target-player-sequence-owner-contract',[row.name for row in unrelated['steps']])

    @patch("scripts.quick_gate.validate_partition")
    def test_changed_workflows_run_the_generated_contract_early(self, _partition):
        for phase in ("normal", "pre-corpus"):
            with self.subTest(phase=phase):
                changed = build_plan((".github/workflows/ci.yml",), phase=phase)["steps"]
                names = [step.name for step in changed]
                contract = names.index("generated-workflow-contract")
                self.assertLess(contract, names.index("generated-owner-plan"))
                self.assertEqual(
                    "tests.test_generated_artifacts.GeneratedArtifactFinalizationTests."
                    "test_broad_generated_ci_uses_the_canonical_finalizer_only",
                    changed[contract].command[-1],
                )
                docs = build_plan(("docs/index.md",), phase=phase)["steps"]
                self.assertNotIn("generated-workflow-contract", [step.name for step in docs])

    def test_normal_inventory_contains_the_same_pre_corpus_obligations(self):
        paths=("quorune/compiler/collection_quantity_templates.py", "tests/test_quick_gate.py")
        pre=build_plan(paths,phase="pre-corpus")
        normal=build_plan(paths,phase="normal")
        inventory={step.name:step for step in normal["steps"]}
        self.assertIn("explicit-text-encoding",inventory)
        for step in pre["steps"]:
            with self.subTest(step=step.name):
                self.assertEqual(step,inventory[step.name])

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
