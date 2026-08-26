from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from quorune.rules_corpus import (
    CORPUS_OPERATIONS,
    execute_rules_corpus_operation,
    rules_next,
)
from quorune.rules_scheduler import (
    RulesSchedulerError,
    build_rules_dependency_queue,
    build_rules_dependency_queue_from_root,
    load_rules_dependency_queue,
    rules_dependency_queue_errors,
)
from quorune.work_selection import (
    WorkSelectionError,
    build_work_selection,
    load_work_selection_inputs,
    selected_work_candidate,
)
from quorune.work_selection_bundles import (
    bundle_measurement_decision,
    candidate_frontier_measurements,
    WorkSelectionBundleError,
)
from quorune.util import stable_json
from scripts.harvest_outcome_history import (
    _semantic_outcome_state,
    build_harvest_outcome_history,
    HarvestOutcomeHistoryError,
)
from scripts.update_rules_scheduler import _compact_markdown


ROOT = Path(__file__).resolve().parents[1]


def _json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _bounded_candidate_bundle_fixture():
    member_ids = [
        "keyword_dependency:fixture-a",
        "keyword_dependency:fixture-b",
    ]
    frontier = {
        "family_candidates": [
            {
                "family_id": family_id,
                "lowerable_untrusted_abilities": 1,
                "occurrences": 1,
                "prerequisites": [],
                "interaction_risk": "low",
            }
            for family_id in member_ids
        ],
        "cards": [
            {
                "minimum_known_blocker_set": [family_id],
                "abilities": [
                    {
                        "blockers": {
                            "canonical_family_ids": [family_id]
                        },
                        "residuals": [{"family_ids": [family_id]}],
                    }
                ],
            }
            for family_id in member_ids
        ],
    }
    policies = [
        {
            "bundle_id": "bundle:bounded-fixture",
            "member_family_ids": member_ids,
            "estimated_implementation_hours": 1,
            "estimated_probe_hours": 1,
            "estimated_generation_hours": 1,
            "measurement_status": "bounded_executable",
        }
    ]
    weights = {
        "complete_card": 1,
        "exact_ability": 1,
        "material_residual": 1,
        "one_additional_blocker_card": 0,
        "two_additional_blocker_card": 0,
    }
    return frontier, policies, weights


def _reviewed_frontier_comparisons(inputs):
    frontier = inputs["card_unlock_frontier"]["family_candidates"]
    frontier_family_ids = {
        str(row["family_id"])
        for row in frontier
    }
    family_ids = {"effect_clause:destroy-target"}
    bundle_member_ids = {
        member
        for bundle in _json("platform/rules-subsystems.json")[
            "work_selection"
        ]["coverage_family"]["candidate_bundles"]
        for member in bundle["member_family_ids"]
        if member in frontier_family_ids
    }
    family_ids.update(
        selected_over.removeprefix("frontier:")
        for row in _json("platform/rules-subsystems.json")[
            "work_selection"
        ]["reviewed_rerank_history"]
        if (selected_over := str(row["selected_over"])).startswith(
            "frontier:"
        )
    )
    family_ids.update(bundle_member_ids)
    comparisons = []
    for row in frontier:
        family_id = str(row["family_id"])
        if family_id not in family_ids:
            continue
        comparison = deepcopy(row)
        if (
            family_id != "effect_clause:destroy-target"
            and family_id not in bundle_member_ids
        ):
            comparison["prerequisites"] = [
                *comparison.get("prerequisites", []),
                "fixture:reviewed-rerank-context",
            ]
        comparisons.append(comparison)
    if {row["family_id"] for row in comparisons} != family_ids:
        raise AssertionError(
            "Synthetic scheduler inputs must preserve reviewed frontier "
            "comparisons"
        )
    return comparisons


def _with_dependency_ready_compiler_harvest(inputs):
    updated = deepcopy(inputs)
    updated["reusable_piece_interactions"]["pairs"] = [
        row
        for row in updated["reusable_piece_interactions"]["pairs"]
        if not (
            row.get("high_risk") is True
            and "fail_closed_runtime_admission"
            in row.get("evidence_assurance_kinds", [])
        )
    ]
    updated["card_unlock_frontier"]["family_candidates"] = [
        {
            "family_id": "effect_clause:fixture-harvest",
            "base_family": "effect_clause:fixture-harvest",
            "expected_exact_card_gain": 50,
            "estimated_effort": "medium",
            "prerequisites": [],
            "runtime_compiler_readiness": "missing_lowering",
            "affected_cards": 60,
            "sole_blocker_cards": 50,
            "one_additional_blocker_cards": 10,
            "two_additional_blocker_cards": 0,
            "expected_exact_ability_gain": 60,
            "expected_material_residual_gain": 60,
            "interaction_risk": "medium",
        },
        *_reviewed_frontier_comparisons(updated),
    ]
    return updated


def _with_large_ability_compiler_harvest(inputs):
    updated = _with_dependency_ready_compiler_harvest(inputs)
    updated["card_unlock_frontier"]["family_candidates"] = [
        {
            "family_id": "effect_clause:large-ability-fixture",
            "base_family": "effect_clause:large-ability-fixture",
            "expected_exact_card_gain": 21,
            "estimated_effort": "large",
            "prerequisites": [],
            "runtime_compiler_readiness": "missing_lowering",
            "affected_cards": 521,
            "sole_blocker_cards": 21,
            "one_additional_blocker_cards": 109,
            "two_additional_blocker_cards": 86,
            "expected_exact_ability_gain": 130,
            "expected_material_residual_gain": 130,
            "interaction_risk": "high",
        },
        *updated["card_unlock_frontier"]["family_candidates"],
    ]
    return updated


class RulesSchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rule_index = _json("rules/rule-index.json")
        cls.conformance = _json("rules/conformance-cases.json")
        cls.catalog = _json("platform/rules-subsystems.json")
        cls.capabilities = _json(
            "quorune/rules/capability-registry.json"
        )
        cls.queue = load_rules_dependency_queue(ROOT)
        cls.work_inputs = load_work_selection_inputs(ROOT)

    def test_generated_queue_is_fresh_and_source_pinned(self):
        self.assertEqual([], rules_dependency_queue_errors(ROOT))
        self.assertEqual(
            self.queue,
            build_rules_dependency_queue_from_root(ROOT),
        )
        self.assertEqual(
            self.rule_index["source_sha256"],
            self.queue["source_sha256"],
        )
        self.assertEqual(
            len(self.rule_index["rules"]),
            self.queue["summary"]["total_rules"],
        )

    def test_every_untrusted_or_unclassified_candidate_is_queued_once(self):
        expected = {
            str(case["rule_id"])
            for case in self.conformance["cases"]
            if case["status"] in {"blocked", "unreviewed"}
            and case["classification"]
            in {"behavioral", "unclassified"}
        }
        queued = [
            str(rule["rule_id"])
            for subsystem in self.queue["subsystems"]
            for rule in subsystem["rules"]
        ]
        self.assertEqual(len(expected), len(queued))
        self.assertEqual(len(queued), len(set(queued)))
        self.assertEqual(expected, set(queued))
        reviewed_blocked = sum(
            case["status"] == "blocked"
            and case["classification"] == "behavioral"
            for case in self.conformance["cases"]
        )
        review_required = sum(
            case["status"] == "unreviewed"
            and case["classification"]
            in {"behavioral", "unclassified"}
            for case in self.conformance["cases"]
        )
        self.assertEqual(
            reviewed_blocked,
            self.queue["summary"]["reviewed_behavioral_blocked"],
        )
        self.assertEqual(
            review_required,
            self.queue["summary"]["behavioral_review_required"],
        )

    def test_subsystems_cover_every_section_once_in_dependency_order(self):
        indexed_sections = {
            str(rule["section"]["id"])
            for rule in self.rule_index["rules"]
        }
        scheduled_sections = [
            str(section_id)
            for subsystem in self.queue["subsystems"]
            for section_id in subsystem["section_ids"]
        ]
        self.assertEqual(147, len(scheduled_sections))
        self.assertEqual(
            len(scheduled_sections), len(set(scheduled_sections))
        )
        self.assertEqual(indexed_sections, set(scheduled_sections))
        positions = {
            subsystem["subsystem_id"]: subsystem["schedule_order"]
            for subsystem in self.queue["subsystems"]
        }
        for subsystem in self.queue["subsystems"]:
            for dependency in subsystem["depends_on_subsystems"]:
                self.assertLess(
                    positions[dependency],
                    positions[subsystem["subsystem_id"]],
                )

    def test_each_queue_item_carries_required_work_context(self):
        for subsystem in self.queue["subsystems"]:
            self.assertTrue(subsystem["compiler_impact"])
            for rule in subsystem["rules"]:
                self.assertTrue(rule["active_profiles"])
                self.assertTrue(rule["compiler_impact"])
                self.assertIn(
                    rule["work_state"],
                    {
                        "behavioral_review_required",
                        "blocked_by_queued_rule",
                        "reviewed_behavioral_blocked",
                    },
                )
                self.assertIsInstance(
                    rule["implementation_components"], list
                )
                self.assertIsInstance(rule["executable_test_ids"], list)
                self.assertIsInstance(rule["dependency_rule_ids"], list)
                self.assertNotIn("text", rule)
                self.assertNotIn("short_summary", rule)

    def test_selected_batch_is_dependency_ready_and_cli_next_uses_it(self):
        selected = self.queue["selected_batch"]
        self.assertEqual(
            "counter-producer-replacement-closure",
            selected["batch_id"],
        )
        self.assertEqual(
            "replacement-prevention", selected["subsystem_id"]
        )
        self.assertEqual(
            {"614.16"},
            set(selected["rule_ids"]),
        )
        self.assertTrue(
            all(
                rule["reviewed"]
                and rule["classification"] == "behavioral"
                and rule["conformance_status"] == "blocked"
                and rule["work_state"] == "reviewed_behavioral_blocked"
                for rule in selected["rules"]
            )
        )
        next_batch = rules_next(ROOT, limit=20)
        self.assertEqual(
            self.queue["fingerprint"],
            next_batch["scheduler_fingerprint"],
        )
        self.assertEqual(
            selected["rule_ids"],
            [rule["rule_id"] for rule in next_batch["next"]],
        )
        selected_work_id = self.queue["work_selection"][
            "selected_candidate_id"
        ]
        if selected_work_id is None:
            self.assertEqual(
                0,
                self.queue["work_selection"]["eligible_candidate_count"],
            )
            self.assertIsNone(next_batch["selected_work"])
        else:
            self.assertEqual(
                selected_work_id,
                next_batch["selected_work"]["candidate_id"],
            )
            self.assertNotIn(
                "reusable_piece_ids", next_batch["selected_work"]
            )
            selected_work = next(
                candidate
                for candidate in self.queue["work_selection"]["candidates"]
                if candidate["candidate_id"] == selected_work_id
            )
            self.assertEqual(
                len(selected_work["reusable_piece_ids"]),
                next_batch["selected_work"]["reusable_piece_count"],
            )
        self.assertIn("queue", CORPUS_OPERATIONS)
        self.assertEqual(
            self.queue["fingerprint"],
            execute_rules_corpus_operation(
                "queue", root=ROOT
            )["fingerprint"],
        )

    def test_catalog_rejects_duplicate_sections_dependency_cycles_and_bad_batch(self):
        duplicate = deepcopy(self.catalog)
        duplicate["subsystems"][1]["section_ids"].append("100")
        with self.assertRaisesRegex(
            RulesSchedulerError, "assigned more than once"
        ):
            build_rules_dependency_queue(
                self.rule_index,
                self.conformance,
                duplicate,
                self.capabilities,
            )

        completed_selection = deepcopy(self.catalog)
        completed_selection["selected_batch"] = {
            "batch_id": "typed-ordinary-cycling-activation",
            "subsystem_id": "keyword-abilities",
            "rule_ids": ["702.29a", "702.29b"],
            "target_capability_ids": ["activation.cycling.hand"],
            "exit_criteria": ["The bounded family is already complete."],
        }
        with self.assertRaisesRegex(
            RulesSchedulerError,
            "already complete",
        ):
            build_rules_dependency_queue(
                self.rule_index,
                self.conformance,
                completed_selection,
                self.capabilities,
                repository_root=ROOT,
            )

        cycle = deepcopy(self.catalog)
        cycle["subsystems"][0]["depends_on"] = ["formats"]
        with self.assertRaisesRegex(RulesSchedulerError, "cycle"):
            build_rules_dependency_queue(
                self.rule_index,
                self.conformance,
                cycle,
                self.capabilities,
            )

        trusted_selection = deepcopy(self.catalog)
        trusted_selection["selected_batch"]["rule_ids"] = ["614.5"]
        with self.assertRaisesRegex(
            RulesSchedulerError,
            "trusted, definition-only, or unknown rule",
        ):
            build_rules_dependency_queue(
                self.rule_index,
                self.conformance,
                trusted_selection,
                self.capabilities,
            )

    def test_cross_program_selection_ranks_correctness_before_card_gain(self):
        inputs = _with_dependency_ready_compiler_harvest(self.work_inputs)
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=inputs,
        )
        selected = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"] == work["selected_candidate_id"]
        )
        self.assertEqual(
            [
                "ci_correctness",
                "replay_privacy_defect",
                "prohibited_runtime_semantics",
                "architecture_owner_or_mutation_defect",
                "interaction_assurance",
                "rules_foundation",
                "compiler_harvest",
                "card_family",
            ],
            work["priority_classes"],
        )
        runtime_total = int(
            inputs["architecture_audit"]["architecture"]
            ["runtime_oracle_text_access"]
            ["prohibited_runtime_interpretation_count"]
        )
        if runtime_total == 0:
            self.assertFalse(
                any(
                    candidate["candidate_class"]
                    == "prohibited_runtime_semantics"
                    for candidate in work["candidates"]
                )
            )
            assurance = next(
                candidate
                for candidate in work["candidates"]
                if candidate["candidate_id"]
                == "assurance:critical-interaction-recovery"
            )
            debt = assurance["interaction_debt_introduced"]
            gate_open = (
                debt["uncovered_high_risk_pairs"]
                > debt["starting_uncovered_high_risk_pairs"]
            )
            card_candidates = [
                candidate
                for candidate in work["candidates"]
                if candidate["candidate_class"]
                in {"compiler_harvest", "card_family"}
                and candidate["eligible"]
            ]
            self.assertTrue(card_candidates)
            if gate_open:
                self.assertEqual(assurance, selected)
                self.assertTrue(
                    all(
                        selected["rank"] < candidate["rank"]
                        for candidate in card_candidates
                    )
                )
            else:
                self.assertFalse(assurance["eligible"])
                self.assertEqual(
                    "exit_gate_satisfied",
                    assurance["assurance_readiness"]["status"],
                )
                self.assertIn(
                    selected["candidate_class"],
                    {"compiler_harvest", "card_family"},
                )
            return
        self.assertEqual(
            "prohibited_runtime_semantics", selected["candidate_class"]
        )
        self.assertNotEqual(
            "cross_subsystem_runtime_semantics",
            selected["universal_subsystem"],
        )
        selected_runtime_count = int(
            selected["runtime_oracle_text_removal"]["expected_count"]
        )
        self.assertGreater(selected_runtime_count, 0)
        if (
            selected["candidate_id"]
            == "architecture:runtime-oracle-text-subsystem-attribution"
        ):
            self.assertEqual(runtime_total, selected_runtime_count)
            self.assertEqual(
                "not_applicable", selected["compiler_readiness"]["status"]
            )
            self.assertIn(
                "do not treat the remainder as one implementation batch",
                selected["reranking_reason"],
            )
        else:
            runtime_candidates = [
                candidate
                for candidate in work["candidates"]
                if candidate["candidate_class"]
                == "prohibited_runtime_semantics"
            ]
            if len(runtime_candidates) == 1:
                self.assertEqual(
                    selected["candidate_id"],
                    runtime_candidates[0]["candidate_id"],
                )
                self.assertEqual(runtime_total, selected_runtime_count)
            else:
                self.assertLess(selected_runtime_count, runtime_total)
        self.assertGreater(selected["priority_within_class"], 0)
        card_candidates = [
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_class"]
            in {"compiler_harvest", "card_family"}
            and candidate["eligible"]
        ]
        self.assertTrue(card_candidates)
        self.assertGreater(
            max(
                int(candidate["expected_complete_card_gain"] or 0)
                for candidate in card_candidates
            ),
            int(selected["expected_complete_card_gain"] or 0),
        )
        self.assertTrue(
            all(selected["rank"] < candidate["rank"] for candidate in card_candidates)
        )

    def test_assurance_exit_gate_accepts_baseline_and_resumes_card_harvest(self):
        inputs = _with_dependency_ready_compiler_harvest(self.work_inputs)
        interaction = inputs["reusable_piece_delta"]["interaction_coverage"]
        baseline = self.catalog["work_selection"]["interaction_assurance"][
            "starting_uncovered_high_risk_pairs"
        ]
        self.assertEqual(0, baseline)
        interaction["covered_high_risk_pairs"] = (
            interaction["applicable_high_risk_pairs"] - baseline
        )

        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=inputs,
        )
        assurance = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"]
            == "assurance:critical-interaction-recovery"
        )
        selected = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"] == work["selected_candidate_id"]
        )

        self.assertFalse(assurance["eligible"])
        self.assertEqual(
            "exit_gate_satisfied",
            assurance["assurance_readiness"]["status"],
        )
        self.assertIn(
            "no uncovered high-risk interaction debt remains",
            assurance["reranking_reason"],
        )
        self.assertEqual("compiler_harvest", selected["candidate_class"])
        self.assertIn(
            selected["candidate_id"],
            {
                f"frontier:{row['family_id']}"
                for row in inputs["card_unlock_frontier"]["family_candidates"]
            },
        )

    def test_complete_card_gain_outranks_large_ability_only_harvest(self):
        inputs = _with_large_ability_compiler_harvest(self.work_inputs)
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=inputs,
        )
        selected = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"] == work["selected_candidate_id"]
        )

        self.assertEqual(
            "frontier:effect_clause:fixture-harvest",
            selected["candidate_id"],
        )
        self.assertEqual("compiler_harvest", selected["candidate_class"])
        narrow = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"]
            == "frontier:effect_clause:large-ability-fixture"
        )
        self.assertFalse(narrow["eligible"])
        self.assertEqual(
            "requires_broader_bundle",
            narrow["runtime_readiness"]["status"],
        )
        self.assertEqual(21, narrow["expected_complete_card_gain"])
        self.assertEqual(130, narrow["expected_exact_ability_gain"])
        self.assertEqual(
            [
                "expected_complete_card_gain",
                "expected_exact_ability_gain",
                "expected_material_residual_reduction",
            ],
            work["selection_policy"]["coverage_rank_order"],
        )

    def test_prerequisite_exception_requires_measured_fanout_and_open_budget(self):
        inputs = _with_large_ability_compiler_harvest(self.work_inputs)
        prerequisite = next(
            row
            for row in inputs["card_unlock_frontier"]["family_candidates"]
            if row["family_id"] == "effect_clause:large-ability-fixture"
        )
        prerequisite["expected_exact_ability_gain"] = 80
        prerequisite["expected_material_residual_gain"] = 80
        policy = deepcopy(self.catalog["work_selection"])
        policy["coverage_family"][
            "maximum_consecutive_prerequisite_exceptions"
        ] = 3
        policy["coverage_family"]["approved_prerequisite_exceptions"] = [
            {
                "candidate_id": "frontier:effect_clause:large-ability-fixture",
                "expected_downstream_complete_card_gain": 130,
                "reason": (
                    "The fixture represents a measured shared prerequisite for a "
                    "larger immediate card harvest."
                ),
            }
        ]
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=policy,
            inputs=inputs,
        )
        narrow = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"]
            == "frontier:effect_clause:large-ability-fixture"
        )
        self.assertTrue(narrow["eligible"])
        self.assertEqual(
            "approved_prerequisite_exception",
            narrow["runtime_readiness"]["status"],
        )

        policy["coverage_family"][
            "maximum_consecutive_prerequisite_exceptions"
        ] = 2
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=policy,
            inputs=inputs,
        )
        narrow = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"]
            == "frontier:effect_clause:large-ability-fixture"
        )
        self.assertFalse(narrow["eligible"])
        self.assertEqual(
            "requires_broader_bundle",
            narrow["runtime_readiness"]["status"],
        )
        self.assertEqual(
            2,
            work["selection_policy"]["consecutive_subthreshold_harvests"],
        )

    def test_harvest_history_exposes_repeated_subthreshold_results(self):
        inputs = _with_dependency_ready_compiler_harvest(self.work_inputs)
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=inputs,
        )
        calibration = work["selection_policy"]
        history = self.work_inputs["harvest_outcome_history"]["entries"]
        minimum_gain = self.catalog["work_selection"]["coverage_family"][
            "minimum_complete_card_gain"
        ]
        expected_subthreshold = sum(
            row["actual_complete_card_gain"] < minimum_gain
            for row in history
        )
        expected_error = sum(
            abs(
                row["expected_complete_card_gain"]
                - row["actual_complete_card_gain"]
            )
            for row in history
            if row["expected_complete_card_gain"] is not None
        )
        expected_consecutive = 0
        for row in reversed(history):
            if row["actual_complete_card_gain"] >= minimum_gain:
                break
            expected_consecutive += 1

        self.assertEqual(
            len(history), calibration["observed_harvest_count"]
        )
        self.assertEqual(
            expected_subthreshold,
            calibration["observed_subthreshold_harvest_count"],
        )
        self.assertEqual(
            expected_error,
            calibration["observed_card_gain_absolute_error"],
        )
        self.assertEqual(
            expected_consecutive,
            calibration["consecutive_subthreshold_harvests"],
        )

    def test_harvest_history_is_derived_from_immutable_git_receipts(self):
        provenance = self.catalog["work_selection"]["harvest_provenance"]
        self.assertTrue(provenance)
        self.assertTrue(
            all(
                not any(str(field).startswith("actual_") for field in row)
                for row in provenance
            )
        )
        derived = build_harvest_outcome_history(
            ROOT,
            provenance,
            self.catalog["work_selection"][
                "semantic_transition_declaration"
            ],
        )
        self.assertEqual(
            self.work_inputs["harvest_outcome_history"], derived
        )
        latest = derived["entries"][-1]
        self.assertEqual("bundle:fixed-impulse-access", latest["bundle_id"])
        self.assertEqual(22, latest["actual_complete_card_gain"])
        self.assertEqual(41, latest["oracle_exact_ability_node_delta"])
        self.assertEqual(24, latest["card_program_ability_record_delta"])
        self.assertEqual(
            1,
            latest["architecture_delta"][
                "commander_engine_logical_lines"
            ],
        )
        self.assertNotEqual(
            latest["base_receipt"]["blobs"][
                "coverage/card-unlock-frontier.json.gz"
            ]["git_blob_oid"],
            latest["head_receipt"]["blobs"][
                "coverage/card-unlock-frontier.json.gz"
            ]["git_blob_oid"],
        )

        penultimate = derived["entries"][-2]
        self.assertEqual(
            "bundle:fixed-activated-tap-costs",
            penultimate["bundle_id"],
        )
        self.assertEqual(47, penultimate["actual_complete_card_gain"])
        self.assertEqual(67, penultimate["oracle_exact_ability_node_delta"])
        self.assertEqual(0, penultimate["card_program_ability_record_delta"])

        malformed = deepcopy(provenance)
        malformed[-1]["actual_complete_card_gain"] = 37
        with self.assertRaisesRegex(
            HarvestOutcomeHistoryError, "invalid shape"
        ):
            build_harvest_outcome_history(ROOT, malformed)

    def test_pending_semantic_outcome_blocks_the_next_harvest(self):
        inputs = deepcopy(self.work_inputs)
        history = inputs["harvest_outcome_history"]
        latest = history["entries"][-1]["head_receipt"]
        history["semantic_outcome_status"] = "pending"
        history["pending_transition"] = {
            "transition_id": "fixture-semantic-transition",
            "bundle_id": "bundle:fixture-semantic-transition",
            "candidate_ids": ["compiler:fixture-semantic-transition"],
            "family_ids": ["effect_clause:fixture-semantic-transition"],
            "capability_ids": ["effect.fixture_semantic_transition"],
            "expected_complete_card_gain": 50,
            "non_harvest_reason": None,
            "outcome_kind": "harvest",
            "compiler_version": latest["compiler_version"],
            "card_program_schema_version": latest[
                "card_program_schema_version"
            ],
            "semantic_receipt_sha256": {
                path: latest["blobs"][path]["raw_sha256"]
                for path in (
                    "coverage/card-program-coverage-commander.json",
                    "coverage/oracle-coverage-commander.json",
                    "coverage/card-unlock-frontier.json.gz",
                )
            },
            "support_counts": {
                "oracle_exact_cards": latest["oracle_status_counts"][
                    "exact"
                ],
                "trusted_card_programs": latest["trusted_programs"],
                "capability_closed_card_programs": latest[
                    "capability_closed_programs"
                ],
                "oracle_material_residuals": latest[
                    "oracle_material_residuals"
                ],
                "card_program_material_residuals": latest[
                    "card_program_material_residuals"
                ],
                "card_program_ability_records": latest[
                    "card_program_ability_records"
                ],
                "hard_construction_failures": latest[
                    "hard_construction_failures"
                ],
            },
            "grants_gameplay_trust": False,
            "resolution": (
                "Commit the corpus receipt and materialize its immutable "
                "outcome before selecting another semantic harvest."
            ),
        }
        payload = dict(history)
        payload.pop("fingerprint")
        history["fingerprint"] = hashlib.sha256(
            stable_json(payload).encode("utf-8")
        ).hexdigest()

        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=inputs,
        )
        selected = selected_work_candidate(work)

        self.assertIsNotNone(selected)
        self.assertEqual("ci:materialize-harvest-outcome", selected["candidate_id"])
        self.assertEqual("ci_correctness", selected["candidate_class"])
        self.assertEqual(
            "fixture-semantic-transition",
            work["selection_policy"]["pending_harvest_transition_id"],
        )

    def test_semantic_support_change_requires_a_precise_outcome_declaration(self):
        latest = self.work_inputs["harvest_outcome_history"]["entries"][-1][
            "head_receipt"
        ]
        current = {
            "compiler_version": "oracle-ir-v129",
            "card_program_schema_version": 2,
            "semantic_receipt_sha256": {
                path: identity["raw_sha256"]
                for path, identity in latest["blobs"].items()
                if path
                in {
                    "coverage/card-program-coverage-commander.json",
                    "coverage/oracle-coverage-commander.json",
                    "coverage/card-unlock-frontier.json.gz",
                }
            },
            "support_counts": {
                "oracle_exact_cards": 1,
                "trusted_card_programs": 1,
                "capability_closed_card_programs": 1,
                "oracle_material_residuals": 1,
                "card_program_material_residuals": 1,
                "card_program_ability_records": 1,
                "hard_construction_failures": 0,
            },
        }
        current["semantic_receipt_sha256"][
            "coverage/oracle-coverage-commander.json"
        ] = "f" * 64

        with self.assertRaisesRegex(
            HarvestOutcomeHistoryError,
            "requires one transition declaration",
        ):
            _semantic_outcome_state(latest, current, None)

        status, pending = _semantic_outcome_state(
            latest,
            current,
            {
                "transition_id": "fixture-non-harvest",
                "bundle_id": None,
                "candidate_ids": [],
                "family_ids": [],
                "capability_ids": [],
                "expected_complete_card_gain": None,
                "non_harvest_reason": (
                    "Compiler identity changed without any support-count gain."
                ),
            },
        )
        self.assertEqual("pending", status)
        self.assertEqual("non_harvest", pending["outcome_kind"])
        self.assertFalse(pending["grants_gameplay_trust"])

    def test_fingerprinted_nonviable_measurements_retire_without_trust(self):
        work = self.queue["work_selection"]
        selected = selected_work_candidate(work)

        self.assertIsNone(selected)
        self.assertEqual(0, work["eligible_candidate_count"])
        self.assertEqual(0, work["implementation_eligible_candidate_count"])
        self.assertFalse(
            any(
                row["work_state"] == "cohort_measurement"
                for row in work["candidates"]
            )
        )
        expected = {
            "bundle:fixed-token-creation-contexts": (0, 27, 27),
            "bundle:fixed-exile-contexts": (0, 19, 19),
        }
        for candidate_id, gains in expected.items():
            candidate = next(
                row
                for row in work["candidates"]
                if row["candidate_id"] == candidate_id
            )
            self.assertFalse(candidate["eligible"])
            self.assertFalse(candidate["implementation_eligible"])
            self.assertEqual(
                "measured_nonviable",
                candidate["bundle"]["measurement_status"],
            )
            self.assertEqual(
                gains,
                (
                    candidate["expected_complete_card_gain"],
                    candidate["expected_exact_ability_gain"],
                    candidate["expected_material_residual_reduction"],
                ),
            )
        measured_outcomes = {
            row["measurement_outcome"]["measurement_id"]: row["measurement_outcome"]
            for row in self.catalog["work_selection"]["coverage_family"][
                "candidate_bundles"
            ]
            if row["measurement_outcome"] is not None
        }
        self.assertEqual(
            {
                "measurement:fixed-token-creation-contexts",
                "measurement:fixed-exile-contexts",
            },
            set(measured_outcomes),
        )
        self.assertTrue(
            all(
                outcome["grants_gameplay_trust"] is False
                for outcome in measured_outcomes.values()
            )
        )

    def test_stale_measurement_outcome_reopens_bounded_probe_without_trust(self):
        policy = deepcopy(self.catalog["work_selection"])
        token = next(
            row
            for row in policy["coverage_family"]["candidate_bundles"]
            if row["bundle_id"] == "bundle:fixed-token-creation-contexts"
        )
        token["measurement_outcome"]["frontier_fingerprint"] = "0" * 64
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=policy,
            inputs=self.work_inputs,
        )
        selected = selected_work_candidate(work)

        self.assertIsNotNone(selected)
        self.assertEqual(
            "measurement:fixed-token-creation-contexts",
            selected["candidate_id"],
        )
        self.assertEqual("cohort_measurement", selected["work_state"])
        self.assertFalse(selected["implementation_eligible"])
        self.assertFalse(
            selected["measurement_task"]["grants_gameplay_trust"]
        )
        source_filter = selected["measurement_task"]["source_corpus_filter"]
        self.assertEqual(["unresolved"], source_filter["ability_statuses"])
        self.assertNotIn("lowerable_untrusted_only", source_filter)

    def test_completed_bundle_retires_and_upper_bound_requires_bounded_cohort(self):
        work = self.queue["work_selection"]
        self.assertNotIn(
            "bundle:commander-pairing-keywords",
            {candidate["candidate_id"] for candidate in work["candidates"]},
        )

        frontier, policies, weights = _bounded_candidate_bundle_fixture()
        measurement = candidate_frontier_measurements(
            frontier, policies, weights
        )[0]
        status, reason = bundle_measurement_decision(
            "upper_bound_only",
            measurement["bounded_executable_verified"],
        )

        self.assertTrue(measurement["bounded_executable_verified"])
        self.assertEqual("upper_bound_only", status)
        self.assertIn("only an upper bound", reason)

    def test_bounded_bundle_fails_closed_when_lowerable_census_drifts(self):
        frontier, policies, weights = _bounded_candidate_bundle_fixture()
        measurement = candidate_frontier_measurements(
            frontier, policies, weights
        )[0]
        self.assertTrue(measurement["bounded_executable_verified"])

        frontier["family_candidates"][0][
            "lowerable_untrusted_abilities"
        ] = 0
        measurement = candidate_frontier_measurements(
            frontier, policies, weights
        )[0]
        status, reason = bundle_measurement_decision(
            policies[0]["measurement_status"],
            measurement["bounded_executable_verified"],
        )

        self.assertFalse(measurement["bounded_executable_verified"])
        self.assertEqual("upper_bound_only", status)
        self.assertIn("no longer matches", reason)

    def test_completed_candidate_bundle_retires_when_all_members_disappear(self):
        frontier, policies, weights = _bounded_candidate_bundle_fixture()
        frontier["family_candidates"] = []

        self.assertEqual(
            [],
            candidate_frontier_measurements(frontier, policies, weights),
        )

    def test_partially_missing_candidate_bundle_remains_invalid(self):
        frontier, policies, weights = _bounded_candidate_bundle_fixture()
        frontier["family_candidates"].pop()

        with self.assertRaisesRegex(
            WorkSelectionBundleError,
            "references missing families: keyword_dependency:fixture-b",
        ):
            candidate_frontier_measurements(frontier, policies, weights)

    def test_material_residual_threshold_is_disjunctive(self):
        inputs = deepcopy(self.work_inputs)
        inputs["card_unlock_frontier"]["family_candidates"] = [
            {
                "family_id": "effect_clause:residual-floor-fixture",
                "base_family": "effect_clause",
                "expected_exact_card_gain": 10,
                "estimated_effort": "medium",
                "prerequisites": [],
                "runtime_compiler_readiness": "missing_lowering",
                "affected_cards": 140,
                "sole_blocker_cards": 10,
                "one_additional_blocker_cards": 30,
                "two_additional_blocker_cards": 40,
                "expected_exact_ability_gain": 20,
                "expected_material_residual_gain": 120,
                "lowerable_untrusted_abilities": 120,
                "interaction_risk": "medium",
            },
            *_reviewed_frontier_comparisons(inputs),
        ]
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=inputs,
        )
        candidate = next(
            row
            for row in work["candidates"]
            if row["candidate_id"]
            == "frontier:effect_clause:residual-floor-fixture"
        )

        self.assertTrue(candidate["eligible"])
        self.assertEqual(
            "major_material_residual_harvest",
            candidate["runtime_readiness"]["status"],
        )

    def test_covered_fail_closed_pressure_does_not_create_false_foreground(self):
        inputs = deepcopy(self.work_inputs)
        reviewed_comparisons = _reviewed_frontier_comparisons(inputs)
        inputs["card_unlock_frontier"]["family_candidates"] = [
            {
                "family_id": "effect_clause:major-ability-fixture",
                "base_family": "effect_clause:major-ability-fixture",
                "expected_exact_card_gain": 0,
                "estimated_effort": "large",
                "prerequisites": [],
                "runtime_compiler_readiness": "missing_lowering",
                "affected_cards": 108,
                "sole_blocker_cards": 0,
                "one_additional_blocker_cards": 20,
                "two_additional_blocker_cards": 40,
                "expected_exact_ability_gain": 108,
                "expected_material_residual_gain": 108,
                "lowerable_untrusted_abilities": 108,
                "interaction_risk": "medium",
            },
            *reviewed_comparisons,
        ]
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=inputs,
        )
        candidates = [
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"].startswith(
                "interaction-implementation:"
            )
        ]
        pressure = max(
            candidates,
            key=lambda candidate: candidate["interaction_debt_introduced"][
                "high_risk_fail_closed_pair_incidence"
            ],
        )

        self.assertTrue(candidates)
        selected = selected_work_candidate(work)
        self.assertIsNone(selected)
        structural = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"]
            == "frontier:effect_clause:major-ability-fixture"
        )
        self.assertFalse(structural["eligible"])
        self.assertEqual(
            "structural_nonexecuting",
            structural["runtime_readiness"]["status"],
        )
        self.assertFalse(pressure["eligible"])
        self.assertEqual(
            "safe_but_unimplemented",
            pressure["runtime_readiness"]["status"],
        )
        self.assertGreater(
            pressure["interaction_debt_introduced"][
                "high_risk_fail_closed_pair_incidence"
            ],
            0,
        )
        self.assertIn("does not block", pressure["reranking_reason"])
        architecture = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"]
            == "architecture:engine-mutation-and-specificity-debt"
        )
        self.assertFalse(architecture["eligible"])
        self.assertEqual(
            "rolling_nonblocking",
            architecture["runtime_readiness"]["status"],
        )

    def test_structural_frontier_volume_cannot_become_foreground(self):
        inputs = _with_dependency_ready_compiler_harvest(self.work_inputs)
        inputs["card_unlock_frontier"]["family_candidates"].insert(
            0,
            {
                "family_id": "effect_clause:structural-fixture",
                "base_family": "effect_clause:structural-fixture",
                "expected_exact_card_gain": 0,
                "estimated_effort": "small",
                "prerequisites": [],
                "runtime_compiler_readiness": "missing_lowering",
                "affected_cards": 900,
                "sole_blocker_cards": 0,
                "one_additional_blocker_cards": 400,
                "two_additional_blocker_cards": 300,
                "expected_exact_ability_gain": 900,
                "expected_material_residual_gain": 900,
                "interaction_risk": "medium",
            },
        )
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=inputs,
        )
        structural = next(
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_id"]
            == "frontier:effect_clause:structural-fixture"
        )

        self.assertFalse(structural["eligible"])
        self.assertEqual(
            "structural_nonexecuting",
            structural["runtime_readiness"]["status"],
        )
        self.assertEqual(
            "frontier:effect_clause:fixture-harvest",
            work["selected_candidate_id"],
        )

    def test_scheduler_markdown_represents_no_eligible_candidate(self):
        queue = deepcopy(self.queue)
        work = queue["work_selection"]
        work["selected_candidate_id"] = None
        work["eligible_candidate_count"] = 0
        work["implementation_eligible_candidate_count"] = 0
        for candidate in work["candidates"]:
            candidate["eligible"] = False
            candidate["implementation_eligible"] = False
            if candidate["selection_state"] == "selected":
                candidate["selection_state"] = "blocked"
        markdown = _compact_markdown(queue)
        self.assertIn("Selected cross-program work: `none`", markdown)
        self.assertIn("Selected work class: `none`", markdown)
        self.assertIn("Selected work state: `none`", markdown)
        self.assertIn("No serious candidate currently meets", markdown)

    def test_work_selection_selected_candidate_contract_handles_none(self):
        work = deepcopy(self.queue["work_selection"])
        work["selected_candidate_id"] = None
        for candidate in work["candidates"]:
            candidate["eligible"] = False
            candidate["implementation_eligible"] = False
            if candidate["selection_state"] == "selected":
                candidate["selection_state"] = "blocked"
        work["eligible_candidate_count"] = 0
        work["implementation_eligible_candidate_count"] = 0
        self.assertIsNone(selected_work_candidate(work))

        eligible = deepcopy(work)
        eligible["candidates"][0]["eligible"] = True
        eligible["eligible_candidate_count"] = 1
        with self.assertRaisesRegex(
            WorkSelectionError,
            "none are eligible",
        ):
            selected_work_candidate(eligible)

        missing = deepcopy(self.queue["work_selection"])
        missing["selected_candidate_id"] = "fixture:missing"
        with self.assertRaisesRegex(
            WorkSelectionError,
            "one eligible selected row",
        ):
            selected_work_candidate(missing)

        outranked = deepcopy(self.queue["work_selection"])
        selected, implementation = outranked["candidates"][:2]
        outranked["selected_candidate_id"] = selected["candidate_id"]
        selected["eligible"] = True
        selected["implementation_eligible"] = False
        selected["selection_state"] = "selected"
        implementation["eligible"] = True
        implementation["implementation_eligible"] = True
        implementation["selection_state"] = "deferred"
        outranked["eligible_candidate_count"] = 2
        outranked["implementation_eligible_candidate_count"] = 1
        with self.assertRaisesRegex(
            WorkSelectionError,
            "cannot outrank implementation-eligible work",
        ):
            selected_work_candidate(outranked)

    def test_runtime_text_candidates_are_split_by_declared_subsystem(self):
        work = self.queue["work_selection"]
        candidates = [
            candidate
            for candidate in work["candidates"]
            if candidate["candidate_class"]
            == "prohibited_runtime_semantics"
        ]
        self.assertNotIn(
            "architecture:runtime-oracle-text-subsystem-attribution",
            {candidate["candidate_id"] for candidate in candidates},
        )
        runtime_total = int(
            self.work_inputs["architecture_audit"]["architecture"]
            ["runtime_oracle_text_access"]
            ["prohibited_runtime_interpretation_count"]
        )
        self.assertEqual(
            runtime_total,
            sum(
                int(candidate["runtime_oracle_text_removal"]["expected_count"])
                for candidate in candidates
            ),
        )
        subsystems_with_debt = {
            str(capsule["id"])
            for capsule in self.work_inputs["architecture_audit"]
            ["architecture"]["subsystem_capsules"]
            if int(capsule["prohibited_runtime_oracle_text_accesses"]) > 0
        }
        self.assertEqual(
            subsystems_with_debt,
            {candidate["universal_subsystem"] for candidate in candidates},
        )

    def test_every_serious_candidate_carries_auditable_reranking_context(self):
        required = {
            "candidate_id",
            "candidate_class",
            "universal_subsystem",
            "reusable_piece_ids",
            "rules_dependency_ids",
            "compiler_readiness",
            "runtime_readiness",
            "assurance_readiness",
            "affected_commander_cards",
            "sole_blocker_cards",
            "one_additional_blocker_cards",
            "two_additional_blocker_cards",
            "expected_exact_ability_gain",
            "expected_complete_card_gain",
            "expected_material_residual_reduction",
            "interaction_debt_introduced",
            "architecture_debt_removed",
            "direct_write_migration",
            "engine_extraction",
            "runtime_oracle_text_removal",
            "estimated_effort",
            "reranking_reason",
            "eligible",
            "implementation_eligible",
            "work_state",
            "measurement_task",
            "priority_within_class",
            "bundle",
            "rank",
            "selection_state",
        }
        candidates = self.queue["work_selection"]["candidates"]
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertEqual(required, set(candidate))
            self.assertTrue(candidate["reranking_reason"])
            self.assertTrue(candidate["universal_subsystem"])
        history = self.queue["work_selection"]["reviewed_rerank_history"]
        self.assertEqual(
            self.catalog["work_selection"]["reviewed_rerank_history"],
            history,
        )
        candidate_ids = {candidate["candidate_id"] for candidate in candidates}
        for row in history:
            self.assertEqual(
                {"candidate_id", "selected_over", "reason"}, set(row)
            )
            self.assertRegex(row["candidate_id"], r"^(rules|compiler):")
            self.assertIn(row["selected_over"], candidate_ids)
            self.assertNotEqual(row["candidate_id"], row["selected_over"])
            self.assertGreaterEqual(len(row["reason"].split()), 12)

    def test_work_selection_policy_fails_closed(self):
        policy = deepcopy(self.catalog["work_selection"])
        policy["priority_classes"].append(policy["priority_classes"][0])
        with self.assertRaisesRegex(
            WorkSelectionError, "priority classes"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=policy,
                inputs=self.work_inputs,
            )

        policy = deepcopy(self.catalog["work_selection"])
        duplicate_context_bundle = next(
            row
            for row in policy["coverage_family"]["candidate_bundles"]
            if row["measurement_status"] == "measured_nonviable"
        )
        duplicate_context_bundle["source_contexts"].append(
            duplicate_context_bundle["source_contexts"][0]
        )
        with self.assertRaisesRegex(
            WorkSelectionError, "closed identities"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=policy,
                inputs=self.work_inputs,
            )

        policy = deepcopy(self.catalog["work_selection"])
        measured = next(
            row
            for row in policy["coverage_family"]["candidate_bundles"]
            if row["measurement_status"] == "measured_nonviable"
        )
        measured["source_contexts"] = ["spell"]
        with self.assertRaisesRegex(
            WorkSelectionError, "closed identities"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=policy,
                inputs=self.work_inputs,
            )

        policy = deepcopy(self.catalog["work_selection"])
        measured = next(
            row
            for row in policy["coverage_family"]["candidate_bundles"]
            if row["measurement_status"] == "measured_nonviable"
        )
        measured["measurement_outcome"]["grants_gameplay_trust"] = True
        with self.assertRaisesRegex(
            WorkSelectionError, "exact, no-trust"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=policy,
                inputs=self.work_inputs,
            )

        policy = deepcopy(self.catalog["work_selection"])
        measured = next(
            row
            for row in policy["coverage_family"]["candidate_bundles"]
            if row["measurement_status"] == "measured_nonviable"
        )
        measured["measurement_outcome"]["exact_ability_gain"] = 100
        with self.assertRaisesRegex(
            WorkSelectionError, "below every harvest floor"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=policy,
                inputs=self.work_inputs,
            )

        inputs = deepcopy(self.work_inputs)
        inputs["harvest_outcome_history"]["entries"][-1][
            "actual_complete_card_gain"
        ] += 1
        with self.assertRaisesRegex(
            WorkSelectionError, "fingerprint is stale"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=self.catalog["work_selection"],
                inputs=inputs,
            )

        policy = deepcopy(self.catalog["work_selection"])
        policy["reviewed_rerank_history"][0]["selected_over"] = (
            "frontier:missing-completed-candidate"
        )
        with self.assertRaisesRegex(
            WorkSelectionError, "selected_over must reference a current candidate"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=policy,
                inputs=self.work_inputs,
            )

        policy = deepcopy(self.catalog["work_selection"])
        policy["coverage_family"]["approved_prerequisite_exceptions"] = [
            {
                "candidate_id": "frontier:missing-prerequisite-fixture",
                "expected_downstream_complete_card_gain": 100,
                "reason": "A missing candidate must never become an exception.",
            }
        ]
        with self.assertRaisesRegex(
            WorkSelectionError, "current serious frontier candidate"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=policy,
                inputs=self.work_inputs,
            )

        with self.assertRaisesRegex(
            RulesSchedulerError, "work-selection inputs"
        ):
            build_rules_dependency_queue(
                self.rule_index,
                self.conformance,
                self.catalog,
                self.capabilities,
                repository_root=ROOT,
            )


if __name__ == "__main__":
    unittest.main()
