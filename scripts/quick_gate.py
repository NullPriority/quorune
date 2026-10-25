from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.change_impact import (
    changed_files,
    changed_python_symbols,
    classify_changes,
)
from scripts.test_shards import load_manifest, suite_modules, validate_partition
from scripts.validate_python_runtime import require_supported_python


@dataclass(frozen=True)
class QuickStep:
    name: str
    command: tuple[str, ...]
    input_text: str | None = None


def implicit_text_encoding_lines(source: str) -> tuple[int, ...]:
    """Catch locale-dependent text reads before cross-platform test execution."""
    return tuple(sorted(node.lineno for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'read_text' and not node.args
        and not any(keyword.arg == 'encoding' for keyword in node.keywords)))


_GENERATED_CHECK_ALIASES = frozenset(
    {
        "capability-evidence",
        "card-unlock-frontier",
        "ci-escape-report",
        "documentation",
        "module-classifications",
        "platform-status",
        "reusable-pieces",
        "rules-scheduler",
        "work-selection-cohort-measurements",
    }
)


def _python() -> str:
    return str(Path(sys.executable).resolve())


def build_plan(
    paths: Sequence[str],
    *,
    changed_symbols: Sequence[str] = (),
    phase: str = "normal",
    base_ref: str = "origin/main",
) -> dict:
    if phase not in {"normal", "pre-corpus"}:
        raise ValueError(f"unsupported quick-gate phase: {phase}")
    impact = classify_changes(paths, changed_symbols=changed_symbols)
    manifest = load_manifest()
    validate_partition(manifest)
    modules = list(impact.test_modules)
    for suite in impact.test_suites:
        modules.extend(suite_modules(manifest, suite))
    selected_modules = tuple(dict.fromkeys(modules))
    python = _python()
    database = ROOT / "local" / "quick-gate" / "test-ci.sqlite3"
    steps = [
        QuickStep("python-runtime", (python, "scripts/validate_python_runtime.py")),
        QuickStep(
            "compile",
            (
                python,
                "-m",
                "compileall",
                "-q",
                "quorune",
                "server",
                "tests",
                "scripts",
                "simctl.py",
            ),
        ),
    ]
    if phase == "pre-corpus":
        steps.extend(
            (
                QuickStep("explicit-text-encoding", (python, "scripts/quick_gate.py", "--validate-text-io", "--changed-files-stdin"),
                    input_text=json.dumps([path for path in paths if path.endswith('.py')])),
                QuickStep(
                    "mechanic-contract-source",
                    (python, "-c", "from scripts.update_card_unlock_frontier import _contracts; _contracts()"),
                ),
                QuickStep(
                    "generated-owner-plan",
                    (
                        python,
                        "scripts/cloud_generated_artifacts.py",
                        "plan",
                        "--base-ref",
                        base_ref,
                    ),
                ),
                QuickStep(
                    "compiler-identity",
                    (
                        python,
                        "scripts/cloud_generated_artifacts.py",
                        "verify-compiler-identity",
                        "--base-ref",
                        base_ref,
                    ),
                ),
                QuickStep(
                    "architecture-policy",
                    (
                        python,
                        "scripts/validate_architecture.py",
                        "--source-check",
                    ),
                ),
                QuickStep(
                    "capability-evidence-declarations",
                    (
                        python,
                        "scripts/update_capability_evidence.py",
                        "--validate-declarations",
                    ),
                ),
                QuickStep(
                    "rules-selection-policy",
                    (
                        python,
                        "scripts/update_rules_scheduler.py",
                        "--validate-policy",
                    ),
                ),
                QuickStep(
                    "reusable-piece-policy",
                    (
                        python,
                        "scripts/update_reusable_piece_matrix.py",
                        "--validate-policy",
                    ),
                ),
                QuickStep(
                    "change-impact-contract",
                    (
                        python,
                        "-m",
                        "unittest",
                        (
                            "tests.test_ci_pipeline.CiPipelineTests."
                            "test_risk_plan_selects_owned_modules_and_fails_closed"
                        ),
                    ),
                ),
            )
        )
        if {'quorune/compiler/target_effect_corpus_assurance.py',
                'quorune/compiler/overload_spell_faces.py', 'quorune/compiler/cleave_spell_faces.py',
                'quorune/compiler/paired_spell_faces.py'}.intersection(paths):
            steps.append(QuickStep(
                "paired-spell-target-corpus-carrier-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_fixed_target_effect_sequences.py", "-k",
                 "test_corpus_assurance_validates_both_source_bound_paid_spell_variants"),
            ))
        if {'quorune/compiler/cycle_or_discard_bindings.py',
                'quorune/compiler/fixed_public_action_event_bindings.py'}.intersection(paths):
            steps.append(QuickStep(
                "combined-discard-public-action-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_fixed_counter_event_triggers.py", "-k",
                 "test_public_action_event_suffixes_remain_fail_closed"),
            ))
        if {'quorune/morph.py', 'scripts/work_selection_cohort_measurements.py'}.intersection(paths):
            steps.append(QuickStep(
                "hybrid-face-down-probe-contract",
                (python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_rules_scheduler.py", "-k",
                 "test_fixed_face_down_lifecycle_probe_is_closed"),
            ))
        if {'quorune/rules/casting/fixed_text_variant_costs.py',
                'quorune/rules/casting/overload_costs.py'}.intersection(paths):
            steps.append(QuickStep(
                "reviewed-legacy-text-alternative-contract",
                (python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_fixed_overload.py", "-k",
                 "test_current_reviewed_legacy_alternative_keeps_its_declared_cost_owner"),
            ))
        if 'quorune/compiler/casting_keyword_nodes.py' in paths:
            steps.append(QuickStep(
                "casting-dispatch-omission-contract",
                (python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_fixed_public_alternative_costs.py", "-k",
                 "test_fixed_public_alternative_cost_dependency_and_compiler_mutations_fail_closed"),
            ))
        if "quorune/compiler/library_search_templates.py" in paths:
            steps.append(QuickStep(
                "search-saga-residual-carrier-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_high_risk_interaction_assurance.py", "-k",
                 "test_counted_search_promotion_preserves_saga_reanimation_boundary"),
            ))
        if "test_fixed_counter_event_triggers" in selected_modules:
            steps.append(QuickStep(
                "target-corpus-carrier-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_fixed_counter_event_triggers.py", "-k",
                 "test_tap_state_target_body_is_accepted_by_corpus_assurance"),
            ))
        if {"quorune/compiler/keyword_event_effect_nodes.py", "quorune/compiler/target_effect_corpus_assurance.py"}.intersection(paths):
            steps.append(QuickStep(
                "exploit-target-corpus-carrier-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_fixed_keyword_event_effects.py", "-k",
                 "test_exploit_target_result_is_accepted_by_corpus_assurance_and_rejects_changed_effect"),
            ))
        if "quorune/compiler/bound_effect_programs.py" in paths:
            steps.append(QuickStep(
                "bound-target-prevention-carrier-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_bound_effect_programs.py", "-k",
                 "test_bound_creature_prevention_pronouns_preserve_closed_scope_shape"),
            ))
        if "quorune/compiler/event_card_return_templates.py" in paths:
            steps.append(QuickStep(
                "event-return-source-less-probe-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_event_card_return.py", "-k",
                 "test_source_less_probe_keeps_literal_self_return_closed"),
            ))
        if "scripts/work_selection_cohort_measurements.py" in paths:
            steps.append(QuickStep(
                "batch-measurement-probe-dispatch-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_event_card_return.py", "-k",
                 "test_batch_measurement_probes_reach_the_registered_dispatcher"),
            ))
        if {"quorune/compiler/event_card_return_templates.py", "quorune/card_programs/reviewed_overlay.py"}.intersection(paths):
            steps.append(QuickStep(
                "event-return-legacy-overlay-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_event_card_return.py", "-k",
                 "test_event_return_overlay_requires_identical_self_event_and_bound_sources"),
            ))
        if "scripts/update_work_selection_cohort_measurements.py" in paths:
            steps.append(QuickStep(
                "unmerged-batch-receipt-base-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_rules_scheduler.py", "-k",
                 "test_revised_unmerged_batch_inherits_only_its_verified_receipt_base"),
            ))
        if "scripts/harvest_outcome_history.py" in paths:
            steps.append(QuickStep(
                "unmerged-harvest-revision-identity-contract",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_rules_scheduler.py", "-k",
                 "test_compiler_revision_matches_only_the_same_unmerged_harvest_bundle"),
            ))
        if "test_capability_implementation_mutations" in selected_modules:
            steps.append(QuickStep(
                "mutation-patch-target-integrity",
                (python, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_capability_implementation_mutations.py", "-k",
                 "test_literal_patch_targets_resolve_before_mutation_execution"),
            ))
        quantity_sources = {
            "quorune/compiler/collection_quantity_templates.py",
            "quorune/compiler/query_characteristic_templates.py",
        }
        if quantity_sources.intersection(paths):
            for module, test in (
                ("test_typed_dynamic_characteristics", "test_versioned_codec_keeps_legacy_counts_and_closed_exclusions"),
                ("test_typed_dynamic_characteristics", "test_closed_characteristic_families_compile_exact_and_near_misses_remain_residual"),
                ("test_typed_query_self_characteristics", "test_query_characteristic_grammar_keeps_ambiguous_families_residual"),
                ("test_query_power_toughness_definitions", "test_query_power_toughness_definition_grammar_keeps_open_families_residual"),
                ("test_public_state_condition_compiler", "test_public_condition_closure_compiles_typed_queries_and_facts"),
                ("test_declared_effect_amounts", "test_undefined_x_dynamic_targets_and_open_definitions_remain_residual"),
            ):
                steps.append(QuickStep(
                    "quantity-boundary-" + test,
                    (python, "-m", "unittest", "discover", "-s", "tests",
                     "-p", module + ".py", "-k", test),
                ))
    if phase == "normal" and "compact-ci-dependencies" in impact.checks:
        steps.append(
            QuickStep(
                "compact-ci-dependencies",
                (
                    python,
                    "scripts/build_test_database.py",
                    "validate-ci-dependencies",
                ),
            )
        )
    if phase == "normal" and selected_modules:
        steps.extend(
            (
                QuickStep(
                    "build-test-database",
                    (
                        python,
                        "scripts/build_test_database.py",
                        "build-ci",
                        "--output",
                        str(database),
                    ),
                ),
                QuickStep(
                    "affected-tests",
                    (
                        python,
                        "scripts/test_shards.py",
                        "run-modules",
                        *selected_modules,
                    ),
                ),
            )
        )
    check_commands = {
        "architecture": (python, "scripts/validate_architecture.py", "--check"),
        "continuous-effect-performance": (
            python,
            "scripts/benchmark_continuous_effects.py",
            "--check",
        ),
        "generated-finalization": (
            python,
            "scripts/finalize_generated.py",
            "--check",
        ),
        "capability-evidence": (
            python,
            "scripts/update_capability_evidence.py",
            "--check",
        ),
        "card-unlock-frontier": (
            python,
            "scripts/update_card_unlock_frontier.py",
            "--check",
        ),
        "reusable-pieces": (
            python,
            "scripts/update_reusable_piece_matrix.py",
            "--check",
        ),
        "ci-escape-report": (
            python,
            "scripts/update_ci_escape_report.py",
            "--check",
        ),
        "documentation": (python, "scripts/validate_documentation.py", "--check"),
        "module-classifications": (
            python,
            "scripts/update_module_classifications.py",
            "--check",
        ),
        "platform-status": (
            python,
            "scripts/update_platform_status.py",
            "--check",
        ),
        "repository": (python, "scripts/validate_repository.py"),
        "rules": (python, "simctl.py", "rules", "verify", "--root", "."),
        "rules-scheduler": (
            python,
            "scripts/update_rules_scheduler.py",
            "--check",
        ),
        "test-shards": (python, "scripts/test_shards.py", "validate"),
        "work-selection-cohort-measurements": (
            python,
            "scripts/update_work_selection_cohort_measurements.py",
            "--check",
        ),
    }
    finalizer_selected = "generated-finalization" in impact.checks
    for check in impact.checks:
        if phase == "pre-corpus" and check not in {
            "continuous-effect-performance"
        }:
            continue
        if check == "compact-ci-dependencies":
            continue
        if finalizer_selected and check in _GENERATED_CHECK_ALIASES:
            continue
        command = check_commands.get(check)
        if command is not None:
            steps.append(QuickStep(check, command))
    if phase == "normal" and "browser-build" in impact.checks:
        npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
        if npm is None:
            raise RuntimeError("npm is required for the affected browser build")
        steps.extend(
            (
                QuickStep("browser-dependencies", (npm, "ci", "--prefix", "web")),
                QuickStep(
                    "browser-generated-types",
                    (npm, "run", "generate:types", "--prefix", "web"),
                ),
                QuickStep(
                    "browser-generated-types-clean",
                    ("git", "diff", "--exit-code", "--", "web/src/generated"),
                ),
                QuickStep("browser-build", (npm, "run", "build", "--prefix", "web")),
            )
        )
    return {
        "impact": impact.to_dict(),
        "test_modules": selected_modules if phase == "normal" else (),
        "deferred_test_modules": (
            selected_modules if phase == "pre-corpus" else ()
        ),
        "database": str(database),
        "phase": phase,
        "steps": tuple(steps),
    }


def _run(plan: dict) -> int:
    database = Path(plan["database"])
    database.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["MTG_CARD_DB"] = str(database)
    environment["MTG_PYTHON_EXECUTABLE"] = _python()
    tests = str(ROOT / "tests")
    environment["PYTHONPATH"] = tests + os.pathsep + environment.get("PYTHONPATH", "")
    started = time.monotonic()
    for step in plan["steps"]:
        step_started = time.monotonic()
        print(f"[{step.name}] {' '.join(step.command)}", flush=True)
        run_options = {"cwd": ROOT, "env": environment}
        if step.input_text is not None:
            run_options.update(input=step.input_text,text=True,encoding='utf-8')
        result = subprocess.run(step.command, **run_options)
        if result.returncode:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "failed_step": step.name,
                        "returncode": result.returncode,
                    },
                    sort_keys=True,
                )
            )
            return result.returncode
        print(f"  pass {time.monotonic() - step_started:.3f}s", flush=True)
    print(
        json.dumps(
            {
                "ok": True,
                "duration_seconds": round(time.monotonic() - started, 3),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "test_modules": len(plan["test_modules"]),
                "full_pr_ci_remains_required": True,
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser(
        description="Run deterministic changed-file local validation"
    )
    parser.add_argument("--base", default="origin/main")
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument("--changed-file", action="append", default=[])
    selection.add_argument("--changed-files-stdin", action="store_true")
    parser.add_argument(
        "--phase",
        choices=("normal", "pre-corpus"),
        default="normal",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-text-io", action="store_true")
    args = parser.parse_args()
    if args.changed_files_stdin:
        try:provided=json.load(sys.stdin)
        except (ValueError,UnicodeError) as exc:parser.error(f'Invalid changed-file JSON: {exc}')
        if not isinstance(provided,list) or any(type(path) is not str or not path for path in provided):
            parser.error('Changed-file input must be a JSON array of nonempty paths')
        paths=tuple(provided)
    else:
        paths = tuple(args.changed_file) if args.changed_file else changed_files(args.base, include_worktree=True)
    if args.validate_text_io:
        violations = []
        checked = 0
        for relative in paths:
            path = ROOT / relative
            if path.suffix != '.py' or not path.is_file():
                continue
            checked += 1
            violations.extend({'path': relative, 'line': line, 'reason': 'read_text requires an explicit encoding'}
                for line in implicit_text_encoding_lines(path.read_text(encoding='utf-8')))
        print(json.dumps({'ok': not violations, 'checked_python_files': checked, 'violations': violations}, sort_keys=True))
        return int(bool(violations))
    plan = build_plan(
        paths,
        phase=args.phase,
        base_ref=args.base,
        changed_symbols=(
            ()
            if args.changed_file or args.changed_files_stdin
            else changed_python_symbols(
                args.base,
                include_worktree=True,
            )
        ),
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "impact": plan["impact"],
                    "phase": plan["phase"],
                    "test_modules": plan["test_modules"],
                    "deferred_test_modules": plan["deferred_test_modules"],
                    "steps": [asdict(step) for step in plan["steps"]],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    return _run(plan)


if __name__ == "__main__":
    raise SystemExit(main())
