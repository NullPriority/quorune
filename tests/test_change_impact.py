from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.change_impact import (
    _symbols_for_ranges,
    changed_python_symbols,
    classify_changes,
    load_impact_policy,
)


class ChangeImpactTests(unittest.TestCase):
    def test_rules_compiler_change_selects_compiler_and_evidence(self):
        plan = classify_changes(
            ["quorune/compiler/prevention_templates.py"]
        )
        self.assertIn("compiler-cardprogram", plan.test_suites)
        self.assertIn("capability-evidence", plan.checks)
        self.assertIn("card-unlock-frontier", plan.checks)
        self.assertIn("reusable-pieces", plan.checks)
        self.assertFalse(plan.browser_full)
        self.assertIn("generated-finalization", plan.checks)

    def test_policy_is_versioned_and_fingerprinted(self):
        policy, fingerprint = load_impact_policy()
        self.assertEqual(6, policy["schema_version"])
        self.assertIn("generated-finalization", policy["default_checks"])
        self.assertEqual(64, len(fingerprint))

    def test_compiler_promotion_owners_select_neighboring_expectations(self):
        cases = {
            "quorune/compiler/continuous_templates.py": {
                "test_fixed_query_keyword_grants",
                "test_typed_dynamic_characteristics",
            },
            "quorune/compiler/activated_costs.py": {
                "test_activated_draw_abilities",
                "test_capability_implementation_mutations",
                "test_fixed_counter_placement_effects",
                "test_fixed_nonrepeating_modal_programs",
                "test_fixed_target_effect_sequences",
                "test_high_risk_interaction_assurance",
            },
            "quorune/compiler/activated_zone_change_costs.py": {
                "test_activated_draw_abilities",
                "test_capability_implementation_mutations",
                "test_fixed_counter_placement_effects",
                "test_fixed_nonrepeating_modal_programs",
                "test_fixed_target_effect_sequences",
                "test_high_risk_interaction_assurance",
            },
            "quorune/compiler/saga_chapter_nodes.py": {
                "test_card_program_trust",
                "test_rules_scheduler",
                "test_saga_counter_progression",
            },
            "quorune/self_cast_reductions.py": {
                "test_card_program_trust",
                "test_fixed_spell_cost_reductions",
                "test_rules_scheduler",
            },
            "quorune/compiler/prevention_templates.py": {
                "test_fixed_token_creation_effects",
                "test_high_risk_interaction_assurance",
            },
        }
        for owner, expected in cases.items():
            with self.subTest(owner=owner):
                plan = classify_changes([owner])
                self.assertLessEqual(expected, set(plan.test_modules))
                self.assertTrue(
                    any(
                        rule.startswith("compiler-")
                        and rule.endswith("-expectations")
                        for rule in plan.matched_rule_ids
                    )
                )

    def test_spell_cast_characteristic_contract_selects_both_owner_modules(self):
        for owner in (
            "quorune/compiler/fixed_counter_trigger_nodes.py",
            "quorune/compiler/spell_cast_predicates.py",
            "quorune/rules/casting/commit.py",
            "quorune/rules/spell_cast_events.py",
            "quorune/trigger_discovery.py",
            "tests/fixtures/fixed-typed-event-trigger-cards.json",
        ):
            with self.subTest(owner=owner):
                plan = classify_changes([owner])
                self.assertLessEqual(
                    {
                        "test_fixed_counter_event_triggers",
                        "test_prowess_rules",
                    },
                    set(plan.test_modules),
                )
                self.assertIn(
                    "spell-cast-characteristic-trigger-contract",
                    plan.matched_rule_ids,
                )

    def test_fixed_target_set_sources_select_owner_and_interaction_evidence(self):
        for owner in (
            "quorune/compiler/fixed_homogeneous_target_sets.py",
            "quorune/rules/fixed_homogeneous_target_set_capability_shapes.py",
            "quorune/selection/targeting.py",
            "quorune/semantic_runtime/fixed_target_set_handlers.py",
            "quorune/targets.py",
            "tests/fixtures/fixed-homogeneous-target-set-cards.json",
        ):
            with self.subTest(owner=owner):
                plan = classify_changes([owner])
                self.assertLessEqual(
                    {
                        "test_fixed_homogeneous_target_sets",
                        "test_semantic_handlers",
                        "test_targeting_v070",
                    },
                    set(plan.test_modules),
                )
                self.assertIn(
                    "fixed-homogeneous-target-set-contract",
                    plan.matched_rule_ids,
                )

    def test_fixed_characteristic_sources_select_shared_layer_owners(self):
        for owner in (
            "quorune/compiler/continuous_templates.py",
            "quorune/continuous_effects.py",
            "quorune/effect_runtime/zones_and_attachments.py",
            "quorune/keyword_abilities.py",
            "quorune/rules/fixed_resolution_characteristic_shapes.py",
            "quorune/semantic_runtime/continuous_components.py",
        ):
            with self.subTest(owner=owner):
                plan = classify_changes([owner])
                self.assertLessEqual(
                    {
                        "test_continuous_effect_duration",
                        "test_fixed_query_keyword_grants",
                        "test_rules_scheduler",
                    },
                    set(plan.test_modules),
                )
                self.assertIn(
                    "fixed-resolution-characteristic-effect-contract",
                    plan.matched_rule_ids,
                )

    def test_effective_keyword_owner_selects_all_host_contracts(self):
        plan = classify_changes(["quorune/keyword_abilities.py"])

        self.assertLessEqual(
            {"test_aerial_blocking", "test_haste_rules"},
            set(plan.test_modules),
        )
        self.assertIn(
            "effective-keyword-host-contract",
            plan.matched_rule_ids,
        )

    def test_continuous_runtime_changes_select_performance_contract(self):
        for owner in (
            "quorune/card_programs/runtime.py",
            "quorune/continuous_effects.py",
            "scripts/benchmark_continuous_effects.py",
        ):
            with self.subTest(owner=owner):
                plan = classify_changes([owner])
                self.assertIn(
                    "continuous-effect-performance",
                    plan.checks,
                )
                self.assertIn(
                    "test_continuous_effect_performance",
                    plan.test_modules,
                )
                self.assertIn(
                    "continuous-effect-performance-contract",
                    plan.matched_rule_ids,
                )

    def test_changed_test_module_is_run_exactly(self):
        plan = classify_changes(["tests/test_life_change.py"])
        self.assertEqual(("test_life_change",), plan.test_modules)
        self.assertEqual("ordinary_source", plan.risk_class)

    def test_deleted_test_and_unknown_path_force_high_risk(self):
        deleted = classify_changes(
            ["tests/test_life_change.py"],
            removed_paths=["tests/test_life_change.py"],
        )
        unknown = classify_changes(["unowned/new-surface.txt"])
        self.assertEqual("high_risk_source", deleted.risk_class)
        self.assertEqual("high_risk_source", unknown.risk_class)
        self.assertIn("removed:tests/test_life_change.py", deleted.risk_reasons)
        self.assertIn("unclassified:unowned/new-surface.txt", unknown.risk_reasons)

    def test_governance_and_merge_authority_risk_classes_are_explicit(self):
        docs = classify_changes(["docs/development/ci-pipeline.md"])
        planner = classify_changes(["scripts/ci_plan.py"])
        package = classify_changes(["pyproject.toml"])
        recovery = classify_changes(
            ["quorune/compiler/prevention_templates.py"],
            labels=("main-red-recovery",),
        )
        self.assertEqual("governance_only", docs.risk_class)
        self.assertEqual("high_risk_source", planner.risk_class)
        self.assertEqual("high_risk_source", package.risk_class)
        self.assertTrue(package.package_full)
        self.assertEqual("ordinary_source", recovery.risk_class)
        self.assertNotIn("label:main-red-recovery", recovery.risk_reasons)

    def test_browser_protocol_change_requests_full_browser_gate(self):
        plan = classify_changes(["web/src/protocol.ts"])
        self.assertTrue(plan.browser_full)
        self.assertIn("browser-build", plan.checks)
        self.assertIn("server-replay-privacy", plan.test_suites)

    def test_internal_action_and_choice_modules_do_not_force_browser(self):
        for path in (
            "quorune/rules/action_proposals.py",
            "quorune/semantic_choices/optional_draw.py",
        ):
            with self.subTest(path=path):
                plan = classify_changes([path])
                self.assertFalse(plan.browser_full)

    def test_rules_paths_select_only_their_focused_browser_journey(self):
        cases = {
            "quorune/fixed_mana_abilities.py": ("mana-action",),
            "quorune/declaration_restrictions.py": ("combat",),
            "quorune/drawing/coordinator.py": ("turn-draw",),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                plan = classify_changes([path])
                self.assertFalse(plan.browser_full)
                self.assertEqual(expected, plan.browser_focuses)

    def test_compiler_and_session_only_changes_keep_compact_smoke_only(self):
        for path in (
            "quorune/compiler/damage_templates.py",
            "quorune/session.py",
        ):
            with self.subTest(path=path):
                plan = classify_changes([path])
                self.assertFalse(plan.browser_full)
                self.assertEqual((), plan.browser_focuses)

    def test_engine_changes_force_complete_high_risk_gate(self):
        plan = classify_changes(["quorune/engine.py"])
        self.assertEqual("high_risk_source", plan.risk_class)
        self.assertTrue(plan.browser_full)
        self.assertTrue(plan.windows_full)

    def test_engine_priority_or_yield_symbols_require_complete_browser(self):
        for symbol in (
            "CommanderEngine._grant_priority",
            "CommanderEngine._set_yield",
            "CommanderEngine._record_action_opportunity",
        ):
            with self.subTest(symbol=symbol):
                plan = classify_changes(
                    ["quorune/engine.py"],
                    changed_symbols=(f"quorune/engine.py:{symbol}",),
                )
                self.assertTrue(plan.browser_full)
                self.assertIn(
                    "browser-facing-priority-and-yield",
                    plan.matched_rule_ids,
                )

    def test_changed_line_ranges_resolve_the_smallest_qualified_symbol(self):
        source = """\
class CommanderEngine:
    def _grant_priority(self):
        value = 1
        return value

    def unrelated(self):
        return 2
"""
        self.assertEqual(
            ("CommanderEngine._grant_priority",),
            _symbols_for_ranges(source, ((3, 3, ""),)),
        )

    def test_changed_symbol_discovery_includes_deleted_base_method(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "tests@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Impact Tests"],
                cwd=root,
                check=True,
            )
            module = root / "quorune" / "engine.py"
            module.parent.mkdir()
            module.write_text(
                "class CommanderEngine:\n"
                "    def _grant_priority(self):\n"
                "        return True\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "base"],
                cwd=root,
                check=True,
            )
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                text=True,
                encoding="ascii",
            ).strip()
            module.write_text(
                "class CommanderEngine:\n"
                "    def unrelated(self):\n"
                "        return False\n",
                encoding="utf-8",
            )

            symbols = changed_python_symbols(
                base,
                include_worktree=True,
                root=root,
            )

        self.assertIn(
            "quorune/engine.py:CommanderEngine._grant_priority",
            symbols,
        )

    def test_persistence_and_projection_still_require_complete_browser(self):
        for path in (
            "quorune/persistence.py",
            "quorune/projection.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(classify_changes([path]).browser_full)

    def test_natural_winner_rules_owners_require_soak_group(self):
        for path in (
            "quorune/commander.py",
            "quorune/damage_results.py",
            "quorune/state_based_actions.py",
        ):
            with self.subTest(path=path):
                plan = classify_changes([path])
                self.assertTrue(plan.browser_full)
                self.assertIn(
                    "natural-winner-critical-path", plan.matched_rule_ids
                )

    def test_protection_changes_cover_each_interaction_owner(self):
        plan = classify_changes(["quorune/protection.py"])

        self.assertEqual(
            {
                "compiler-cardprogram",
                "counter-continuous-effects",
                "events-replacement-zone",
                "state-actions-damage",
                "targets-choices-continuations",
            },
            set(plan.test_suites),
        )
        self.assertIn(
            "protection-and-attachment-interactions",
            plan.matched_rule_ids,
        )
        self.assertFalse(plan.browser_full)

    def test_scheduler_sources_select_harvest_contract_fixtures(self):
        for path in (
            "platform/rules-subsystems.json",
            "quorune/work_selection_bundles.py",
            "scripts/harvest_outcome_history.py",
        ):
            with self.subTest(path=path):
                plan = classify_changes([path])
                self.assertIn("test_rules_scheduler", plan.test_modules)
                self.assertIn("rules-scheduler", plan.checks)
                self.assertIn(
                    "scheduler-harvest-contract", plan.matched_rule_ids
                )

    def test_shared_target_sources_select_return_capability_inventory(self):
        for path in (
            "quorune/compiler/direct_target.py",
            "quorune/compiler/return_to_hand_templates.py",
            "quorune/rules/node_capability_shapes.py",
        ):
            with self.subTest(path=path):
                plan = classify_changes([path])
                self.assertIn(
                    "test_targeted_return_to_hand_effect_clauses",
                    plan.test_modules,
                )
                self.assertIn(
                    "targeted-return-capability-contract",
                    plan.matched_rule_ids,
                )

    def test_semantic_transition_sources_select_pr_evidence_contract(self):
        for path in (
            "platform/rules-subsystems.json",
            "scripts/harvest_outcome_history.py",
            "scripts/pr_evidence.py",
        ):
            with self.subTest(path=path):
                plan = classify_changes([path])
                self.assertIn("test_pr_body_policy", plan.test_modules)
                self.assertIn(
                    "semantic-transition-pr-evidence-contract",
                    plan.matched_rule_ids,
                )

    def test_browser_action_and_choice_contracts_are_explicit(self):
        for path in (
            "quorune/rules/action_catalog.py",
            "quorune/choice_forms.py",
            "quorune/projection.py",
        ):
            with self.subTest(path=path):
                plan = classify_changes([path])
                self.assertTrue(plan.browser_full)
                self.assertTrue(plan.browser_full_reasons)

    def test_windows_sensitive_change_requests_full_windows_gate(self):
        plan = classify_changes(["server/launcher.py"])
        self.assertTrue(plan.windows_full)

    def test_workflow_change_exercises_both_platform_gates(self):
        plan = classify_changes([".github/workflows/ci.yml"])
        self.assertTrue(plan.browser_full)
        self.assertTrue(plan.windows_full)
        self.assertIn("generated-validation", plan.test_suites)
        self.assertEqual("high_risk_source", plan.risk_class)

    def test_unknown_core_module_falls_back_to_core_domain(self):
        plan = classify_changes(["quorune/example_future.py"])
        self.assertEqual(("core-domain",), plan.test_suites)

    def test_labels_can_force_expensive_platform_gates(self):
        plan = classify_changes(
            ["README.md"], labels=("browser-full", "windows-full")
        )
        self.assertTrue(plan.browser_full)
        self.assertTrue(plan.windows_full)

    def test_labels_can_select_focused_browser_journeys(self):
        plan = classify_changes(
            ["README.md"],
            labels=("browser-combat", "browser-turn-draw"),
        )
        self.assertFalse(plan.browser_full)
        self.assertEqual(("combat", "turn-draw"), plan.browser_focuses)
        self.assertEqual(("@combat", "@turn-draw"), plan.browser_focus_patterns)


if __name__ == "__main__":
    unittest.main()
