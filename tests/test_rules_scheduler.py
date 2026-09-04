from __future__ import annotations

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

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
    bundle_measurement_fingerprint,
    bundle_measurement_decision,
    candidate_frontier_measurements,
    validate_bundle_policy,
    WorkSelectionBundleError,
)
from quorune.work_selection_evidence import (
    validate_harvest_forecast_correction,
)
from quorune.util import stable_json
from scripts.harvest_outcome_history import (
    _apply_forecast_corrections,
    _content_entry,
    _refresh_content_entry,
    _receipt,
    _receipt_content_fingerprint,
    _require_landed_harvest_head,
    _semantic_blob_sha256,
    _semantic_outcome_state,
    _semantic_report_sha256,
    _validate_content_entry,
    build_harvest_outcome_history,
    HarvestOutcomeHistoryError,
)
from scripts.update_rules_scheduler import _compact_markdown
from scripts.work_selection_cohort_measurements import (
    _attached_quoted_ability_grant_measurement,
    _fixed_activation_zone_change_predicate_measurement,
    _fixed_entry_return_requirement_measurement,
    _matches_probe,
    _matches_query_self_characteristic_probe,
    _matches_typed_public_state_characteristic_query,
    _source_combat_growth_trigger_measurement,
    _spell_history_transformation_measurement,
)


ROOT = Path(__file__).resolve().parents[1]


def _json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _refingerprint(value: dict) -> None:
    unsigned = dict(value)
    unsigned.pop("fingerprint", None)
    value["fingerprint"] = hashlib.sha256(
        stable_json(unsigned).encode("utf-8")
    ).hexdigest()


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
            "measurement_probe_id": None,
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


def _without_pending_harvest_transition(inputs):
    updated = deepcopy(inputs)
    history = updated["harvest_outcome_history"]
    history["semantic_outcome_status"] = "current"
    history["pending_transition"] = None
    fingerprinted = dict(history)
    fingerprinted.pop("fingerprint")
    history["fingerprint"] = hashlib.sha256(
        stable_json(fingerprinted).encode("utf-8")
    ).hexdigest()
    return updated


def _with_dependency_ready_compiler_harvest(inputs):
    updated = _without_pending_harvest_transition(inputs)
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
        history = inputs["harvest_outcome_history"]
        latest_outcome = history["entries"][-1]
        latest_outcome["actual_complete_card_gain"] = 0
        if "forecast_correction" in latest_outcome:
            latest_outcome["forecast_correction"][
                "certified_complete_card_lower_bound"
            ] = 0
        unsigned_outcome = dict(latest_outcome)
        unsigned_outcome.pop("entry_fingerprint")
        latest_outcome["entry_fingerprint"] = hashlib.sha256(
            stable_json(unsigned_outcome).encode("utf-8")
        ).hexdigest()
        _refingerprint(history)
        prerequisite = next(
            row
            for row in inputs["card_unlock_frontier"]["family_candidates"]
            if row["family_id"] == "effect_clause:large-ability-fixture"
        )
        prerequisite["expected_exact_ability_gain"] = 80
        prerequisite["expected_material_residual_gain"] = 80
        policy = deepcopy(self.catalog["work_selection"])
        minimum_gain = policy["coverage_family"][
            "minimum_complete_card_gain"
        ]
        open_budget = 1
        for outcome in reversed(history["entries"]):
            if outcome["actual_complete_card_gain"] >= minimum_gain:
                break
            open_budget += 1
        policy["coverage_family"][
            "maximum_consecutive_prerequisite_exceptions"
        ] = open_budget
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

        consecutive_subthreshold = work["selection_policy"][
            "consecutive_subthreshold_harvests"
        ]
        policy["coverage_family"][
            "maximum_consecutive_prerequisite_exceptions"
        ] = consecutive_subthreshold
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
            consecutive_subthreshold,
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
            self.catalog["work_selection"].get(
                "semantic_transition_declaration"
            ),
            self.catalog["work_selection"].get("forecast_corrections"),
        )
        self.assertEqual(
            self.work_inputs["harvest_outcome_history"], derived
        )
        by_bundle = {
            entry["bundle_id"]: entry for entry in derived["entries"]
        }
        latest = by_bundle["bundle:fixed-source-event-triggers"]
        self.assertEqual(
            "bundle:fixed-source-event-triggers",
            latest["bundle_id"],
        )
        self.assertEqual(54, latest["actual_complete_card_gain"])
        self.assertEqual(90, latest["oracle_exact_ability_node_delta"])
        self.assertEqual(90, latest["card_program_ability_record_delta"])
        self.assertEqual(
            0,
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

        penultimate = by_bundle["bundle:fixed-combat-state-direct-targets"]
        self.assertEqual(
            "bundle:fixed-combat-state-direct-targets",
            penultimate["bundle_id"],
        )
        self.assertEqual(90, penultimate["actual_complete_card_gain"])
        self.assertEqual(104, penultimate["oracle_exact_ability_node_delta"])
        self.assertEqual(48, penultimate["card_program_ability_record_delta"])

        declaration = self.catalog["work_selection"][
            "semantic_transition_declaration"
        ]
        if declaration["bundle_id"] is None:
            pending = derived["pending_transition"]
            self.assertEqual("pending", derived["semantic_outcome_status"])
            self.assertIsNotNone(pending)
            self.assertEqual("non_harvest", pending["outcome_kind"])
            self.assertEqual(
                declaration["transition_id"], pending["transition_id"]
            )
            self.assertEqual(
                declaration["non_harvest_reason"],
                pending["non_harvest_reason"],
            )
            return
        current = by_bundle[declaration["bundle_id"]]
        self.assertEqual(
            declaration["transition_id"], current["transition_id"]
        )
        self.assertEqual(declaration["candidate_ids"], current["candidate_ids"])
        self.assertEqual(declaration["family_ids"], current["family_ids"])
        self.assertEqual(
            declaration["capability_ids"], current["capability_ids"]
        )
        measurement_receipt = next(
            row
            for row in self.work_inputs["cohort_measurements"][
                "transition_measurements"
            ]
            if row["transition_id"] == declaration["transition_id"]
        )
        measurement = measurement_receipt["measurement"]
        self.assertEqual(
            declaration["measurement_id"], current["measurement_id"]
        )
        self.assertEqual(
            measurement["complete_card_gain"],
            current["expected_complete_card_gain"],
        )
        current_correction = current.get("forecast_correction")
        complete_lower_bound = (
            current_correction["certified_complete_card_lower_bound"]
            if current_correction is not None
            else measurement["complete_card_gain"]
        )
        exact_ability_lower_bound = (
            current_correction["certified_exact_ability_lower_bound"]
            if current_correction is not None
            else measurement["exact_ability_gain"]
        )
        self.assertGreaterEqual(
            current["actual_complete_card_gain"], complete_lower_bound
        )
        self.assertGreaterEqual(
            current["actual_exact_ability_gain"], exact_ability_lower_bound
        )
        if current_correction is not None:
            self.assertEqual(
                measurement["complete_card_gain"],
                current_correction["original_expected_complete_card_gain"],
            )
        self.assertEqual("semantic_content", current["receipt_identity_kind"])

        corrected = by_bundle[
            "bundle:fixed-spell-cast-characteristic-triggers"
        ]
        correction = corrected["forecast_correction"]
        self.assertEqual(
            corrected["expected_complete_card_gain"],
            correction["original_expected_complete_card_gain"],
        )

        malformed_corrections = deepcopy(
            self.catalog["work_selection"]["forecast_corrections"]
        )
        malformed_corrections[0][
            "certified_complete_card_lower_bound"
        ] = 67
        with self.assertRaisesRegex(
            HarvestOutcomeHistoryError, "cannot disappear or mutate"
        ):
            build_harvest_outcome_history(
                ROOT,
                provenance,
                declaration,
                malformed_corrections,
            )

        malformed = deepcopy(provenance)
        malformed[-1]["actual_complete_card_gain"] = 37
        with self.assertRaisesRegex(
            HarvestOutcomeHistoryError, "invalid shape"
        ):
            build_harvest_outcome_history(ROOT, malformed)

    def test_forecast_correction_can_preserve_the_complete_card_bound(self):
        entry = {
            "transition_id": "oracle-ir-v999-secondary-metric-correction",
            "receipt_identity_kind": "semantic_content",
            "expected_complete_card_gain": 53,
            "actual_complete_card_gain": 53,
            "actual_exact_ability_gain": 84,
            "actual_material_residual_reduction": 116,
            "measurement_probe_id": "secondary-metric-probe-v1",
        }
        correction = {
            "transition_id": entry["transition_id"],
            "original_expected_complete_card_gain": 53,
            "certified_complete_card_lower_bound": 53,
            "certified_exact_ability_lower_bound": 84,
            "certified_material_residual_reduction_lower_bound": 84,
            "measurement_probe_id": "secondary-metric-probe-v2",
            "reason": (
                "The integrated v2 probe removes one structural ability-node "
                "false positive while preserving the complete-card bound."
            ),
        }

        _apply_forecast_corrections([entry], [correction])

        self.assertEqual(correction, entry["forecast_correction"])
        self.assertIn("entry_fingerprint", entry)
        self.assertEqual(
            correction,
            validate_harvest_forecast_correction(correction, outcome=entry),
        )
        same_probe = dict(correction)
        same_probe["measurement_probe_id"] = "secondary-metric-probe-v1"
        with self.assertRaisesRegex(
            HarvestOutcomeHistoryError, "contradicts its realized outcome"
        ):
            _apply_forecast_corrections(
                [
                    {
                        key: value
                        for key, value in entry.items()
                        if key not in {"forecast_correction", "entry_fingerprint"}
                    }
                ],
                [same_probe],
            )

    def test_harvest_provenance_rejects_squash_discardable_feature_heads(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)

            def git(*arguments: str) -> str:
                completed = subprocess.run(
                    ["git", *arguments],
                    cwd=repository,
                    check=True,
                    text=True,
                    encoding="utf-8",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                return completed.stdout.strip()

            git("init", "--initial-branch=main")
            git("config", "user.email", "scheduler-test@example.invalid")
            git("config", "user.name", "Rules Scheduler Test")
            (repository / "receipt.txt").write_text("main\n", encoding="utf-8")
            git("add", "receipt.txt")
            git("commit", "-m", "test: seed landed receipt")
            landed = git("rev-parse", "HEAD")
            git("switch", "-c", "feature")
            (repository / "receipt.txt").write_text(
                "feature\n", encoding="utf-8"
            )
            git("commit", "-am", "test: add feature receipt")
            feature = git("rev-parse", "HEAD")

            _require_landed_harvest_head(repository, landed)
            with self.assertRaisesRegex(
                HarvestOutcomeHistoryError,
                "must be landed on the durable main line",
            ):
                _require_landed_harvest_head(repository, feature)

    def test_content_receipts_survive_squash_commit_identity_changes(self):
        provenance = self.catalog["work_selection"]["harvest_provenance"]
        latest = provenance[-1]
        base = _receipt(ROOT, latest["base_commit"])
        head = _receipt(ROOT, latest["head_commit"])
        declaration = {
            "transition_id": "fixture-content-transition",
            "compiler_version": head["compiler_version"],
            "bundle_id": "bundle:fixture-content-transition",
            "candidate_ids": ["compiler:fixture-content-transition"],
            "family_ids": ["effect_clause:fixture-content-transition"],
            "capability_ids": ["effect.fixture_content_transition"],
            "expected_complete_card_gain": 1,
            "non_harvest_reason": None,
            "outcome_kind": "harvest",
        }

        entry = _content_entry(declaration, base=base, head=head)
        validated = _validate_content_entry(entry)

        self.assertEqual(entry, validated)
        self.assertNotIn("commit", entry["base_receipt"])
        self.assertNotIn("commit", entry["head_receipt"])
        self.assertEqual(declaration["family_ids"], entry["family_ids"])
        self.assertEqual(
            declaration["capability_ids"], entry["capability_ids"]
        )

    def test_frontier_semantic_receipt_ignores_database_location_provenance(self):
        first = {
            "schema_version": 1,
            "card_data_snapshot": {
                "schema_version": "2",
                "oracle_source_sha256": "a" * 64,
                "rulings_source_sha256": "b" * 64,
                "oracle_source": r"C:\\local\\oracle.jsonl.gz",
                "rulings_source": r"C:\\local\\rulings.jsonl.gz",
                "bulk_manifest_url": "https://api.scryfall.com/bulk-data",
            },
            "cards": [{"oracle_id": "fixture"}],
            "fingerprint": "c" * 64,
        }
        second = deepcopy(first)
        second["card_data_snapshot"].update(
            {
                "oracle_source": "/cloud/oracle.jsonl.gz",
                "rulings_source": "/cloud/rulings.jsonl.gz",
                "bulk_manifest_url": "rules/manifest.json",
            }
        )
        second["fingerprint"] = "d" * 64

        def encoded(value):
            return gzip.compress(stable_json(value).encode("utf-8"), mtime=0)

        first_identity = _semantic_report_sha256(
            "coverage/card-unlock-frontier.json.gz",
            encoded(first),
            first,
        )
        second_identity = _semantic_report_sha256(
            "coverage/card-unlock-frontier.json.gz",
            encoded(second),
            second,
        )
        changed = deepcopy(second)
        changed["cards"].append({"oracle_id": "semantic-change"})

        self.assertEqual(first_identity, second_identity)
        self.assertNotEqual(
            first_identity,
            _semantic_report_sha256(
                "coverage/card-unlock-frontier.json.gz",
                encoded(changed),
                changed,
            ),
        )

    def test_historical_frontier_blob_upgrades_to_semantic_identity(self):
        latest = self.work_inputs["harvest_outcome_history"]["entries"][-1][
            "head_receipt"
        ]
        identity = dict(
            latest["blobs"]["coverage/card-unlock-frontier.json.gz"]
        )
        expected = identity.pop("semantic_sha256", None)
        if expected is None:
            raw = subprocess.run(
                ["git", "cat-file", "blob", identity["git_blob_oid"]],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            value = json.loads(gzip.decompress(raw))
            expected = _semantic_report_sha256(
                "coverage/card-unlock-frontier.json.gz",
                raw,
                value,
            )

        self.assertEqual(
            expected,
            _semantic_blob_sha256(
                "coverage/card-unlock-frontier.json.gz",
                identity,
                repository=ROOT,
            ),
        )

    def test_measured_content_receipt_preserves_generated_cohort_identity(self):
        provenance = self.catalog["work_selection"]["harvest_provenance"]
        latest = provenance[-1]
        base = _receipt(ROOT, latest["base_commit"])
        head = _receipt(ROOT, latest["head_commit"])
        declaration = {
            "transition_id": "fixture-measured-content-transition",
            "compiler_version": head["compiler_version"],
            "bundle_id": "bundle:fixture-measured-content-transition",
            "candidate_ids": [
                "compiler:fixture-measured-content-transition"
            ],
            "family_ids": [
                "effect_clause:fixture-measured-content-transition"
            ],
            "capability_ids": [
                "effect.fixture_measured_content_transition"
            ],
            "measurement_id": (
                "measurement:fixture-measured-content-transition"
            ),
            "non_harvest_reason": None,
            "outcome_kind": "harvest",
        }
        measurement_receipt = {
            "transition_id": declaration["transition_id"],
            "frontier_fingerprint": "a" * 64,
            "oracle_source_sha256": "b" * 64,
            "measurement": {
                "measurement_id": declaration["measurement_id"],
                "bundle_id": declaration["bundle_id"],
                "probe_id": "fixture-existing-owner-v1",
                "complete_card_gain": 7,
            },
        }
        measurement_receipt["receipt_fingerprint"] = hashlib.sha256(
            stable_json(measurement_receipt).encode("utf-8")
        ).hexdigest()

        entry = _content_entry(
            declaration,
            base=base,
            head=head,
            measurement_receipt=measurement_receipt,
        )

        self.assertEqual(entry, _validate_content_entry(entry))
        self.assertEqual(7, entry["expected_complete_card_gain"])
        self.assertEqual(
            "generated_transition_cohort",
            entry["expected_complete_card_gain_basis"],
        )
        self.assertEqual(
            measurement_receipt["receipt_fingerprint"],
            entry["measurement_receipt_fingerprint"],
        )

    def test_content_receipts_allow_non_harvest_drift_between_transitions(self):
        provenance = self.catalog["work_selection"]["harvest_provenance"]
        penultimate = provenance[-2]
        latest = provenance[-1]
        previous_head = _receipt(ROOT, penultimate["head_commit"])
        base = _receipt(ROOT, latest["base_commit"])
        head = _receipt(ROOT, latest["head_commit"])
        declaration = {
            "transition_id": "fixture-independent-content-transition",
            "compiler_version": head["compiler_version"],
            "bundle_id": "bundle:fixture-independent-content-transition",
            "candidate_ids": [
                "compiler:fixture-independent-content-transition"
            ],
            "family_ids": [
                "effect_clause:fixture-independent-content-transition"
            ],
            "capability_ids": [
                "effect.fixture_independent_content_transition"
            ],
            "expected_complete_card_gain": 1,
            "non_harvest_reason": None,
            "outcome_kind": "harvest",
        }

        self.assertNotEqual(
            _receipt_content_fingerprint(previous_head),
            _receipt_content_fingerprint(base),
        )
        entry = _content_entry(declaration, base=base, head=head)

        self.assertEqual(entry, _validate_content_entry(entry))

    def test_content_receipt_refreshes_downstream_assurance_at_fixed_point(self):
        provenance = self.catalog["work_selection"]["harvest_provenance"]
        latest = provenance[-1]
        base = _receipt(ROOT, latest["base_commit"])
        head = _receipt(ROOT, latest["head_commit"])
        declaration = {
            "transition_id": "fixture-content-fixed-point",
            "compiler_version": head["compiler_version"],
            "bundle_id": "bundle:fixture-content-fixed-point",
            "candidate_ids": ["compiler:fixture-content-fixed-point"],
            "family_ids": ["effect_clause:fixture-content-fixed-point"],
            "capability_ids": ["effect.fixture_content_fixed_point"],
            "expected_complete_card_gain": 1,
            "non_harvest_reason": None,
            "outcome_kind": "harvest",
        }
        entry = _content_entry(declaration, base=base, head=head)
        corrected_head = deepcopy(head)
        corrected_head["interaction_assurance"]["uncovered_high_risk"] = (
            head["interaction_assurance"].get("uncovered_high_risk", 0) + 1
        )
        corrected_head["blobs"][
            "coverage/reusable-piece-interactions.json.gz"
        ]["raw_sha256"] = "a" * 64

        refreshed = _refresh_content_entry(
            entry,
            declaration=declaration,
            head=corrected_head,
        )

        self.assertEqual(refreshed, _validate_content_entry(refreshed))
        self.assertEqual(
            corrected_head["interaction_assurance"],
            refreshed["head_receipt"]["interaction_assurance"],
        )
        self.assertEqual(
            1,
            refreshed["interaction_assurance_delta"]["uncovered_high_risk"],
        )
        self.assertNotEqual(
            entry["entry_fingerprint"], refreshed["entry_fingerprint"]
        )

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
                "compiler_version": "oracle-ir-v129",
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

    def test_generated_nonviable_measurements_retire_without_trust(self):
        expected = {
            "bundle:fixed-token-creation-contexts",
            "bundle:fixed-exile-contexts",
        }
        coverage = self.catalog["work_selection"]["coverage_family"]
        for candidate_id in expected:
            policy = next(
                row
                for row in self.catalog["work_selection"]["coverage_family"]
                ["candidate_bundles"]
                if row["bundle_id"] == candidate_id
            )
            self.assertEqual(
                "generated_probe",
                policy["measurement_status"],
            )
            self.assertEqual(
                {"measurement_probe_id"},
                {
                    field
                    for field in policy
                    if field.startswith("measurement_")
                    and field != "measurement_status"
                },
            )
            outcome = next(
                row
                for row in self.work_inputs["cohort_measurements"][
                    "measurements"
                ]
                if row["bundle_id"] == candidate_id
            )
            self.assertEqual(
                "retired_below_harvest_floor", outcome["decision"]
            )
            self.assertLess(
                outcome["complete_card_gain"],
                coverage["minimum_complete_card_gain"],
            )
            self.assertLess(
                outcome["exact_ability_gain"],
                coverage["minimum_exact_ability_gain"],
            )
            self.assertLess(
                outcome["material_residual_reduction"],
                coverage["minimum_material_residual_reduction"],
            )
            self.assertFalse(outcome["grants_gameplay_trust"])
            self.assertEqual(
                bundle_measurement_fingerprint(
                    self.work_inputs["card_unlock_frontier"], policy
                ),
                outcome["cohort_fingerprint"],
            )
        measured_outcomes = {
            row["measurement_id"]: row
            for row in self.work_inputs["cohort_measurements"]["measurements"]
        }
        expected_measurements = {
            "measurement:fixed-token-creation-contexts",
            "measurement:fixed-exile-contexts",
        }
        self.assertLessEqual(expected_measurements, set(measured_outcomes))
        self.assertTrue(
            all(
                measured_outcomes[measurement_id]["grants_gameplay_trust"] is False
                for measurement_id in expected_measurements
            )
        )

    def test_fixed_public_numeric_damage_target_probe_is_closed(self):
        bundle_id = "bundle:fixed-public-numeric-damage-targets"
        policy = next(
            row
            for row in self.catalog["work_selection"]["coverage_family"][
                "candidate_bundles"
            ]
            if row["bundle_id"] == bundle_id
        )
        self.assertEqual(
            "fixed-public-numeric-damage-target-existing-owner-v1",
            policy["measurement_probe_id"],
        )
        measurement = next(
            row
            for row in self.work_inputs["cohort_measurements"]["measurements"]
            if row["bundle_id"] == bundle_id
        )
        coverage = self.catalog["work_selection"]["coverage_family"]
        transition = next(
            (
                row
                for row in self.work_inputs["cohort_measurements"][
                    "transition_measurements"
                ]
                if row["measurement"]["bundle_id"] == bundle_id
            ),
            None,
        )
        if transition is not None:
            forecast = transition["measurement"]
            self.assertEqual("bounded_executable", forecast["decision"])
            self.assertGreaterEqual(
                forecast["complete_card_gain"],
                coverage["minimum_complete_card_gain"],
            )
            self.assertGreater(forecast["exact_ability_gain"], 0)
            self.assertGreater(
                forecast["material_residual_reduction"],
                forecast["exact_ability_gain"],
            )
            self.assertFalse(forecast["grants_gameplay_trust"])
        else:
            outcome = next(
                row
                for row in reversed(
                    self.work_inputs["harvest_outcome_history"]["entries"]
                )
                if row.get("bundle_id") == bundle_id
            )
            self.assertGreaterEqual(
                outcome["expected_complete_card_gain"],
                coverage["minimum_complete_card_gain"],
            )
            self.assertGreater(outcome["actual_exact_ability_gain"], 0)
            self.assertGreater(
                outcome["actual_material_residual_reduction"],
                outcome["actual_exact_ability_gain"],
            )
        self.assertIn(
            measurement["decision"],
            {"bounded_executable", "retired_below_harvest_floor"},
        )
        if measurement["decision"] == "retired_below_harvest_floor":
            self.assertLess(
                measurement["complete_card_gain"],
                coverage["minimum_complete_card_gain"],
            )
            self.assertLess(
                measurement["exact_ability_gain"],
                coverage["minimum_exact_ability_gain"],
            )
            self.assertLess(
                measurement["material_residual_reduction"],
                coverage["minimum_material_residual_reduction"],
            )
        self.assertFalse(measurement["grants_gameplay_trust"])
        self.assertEqual(
            bundle_measurement_fingerprint(
                self.work_inputs["card_unlock_frontier"],
                policy,
            ),
            measurement["cohort_fingerprint"],
        )

    def test_fixed_optional_effect_probe_requires_a_closed_event_carrier(self):
        probe_id = "fixed-optional-effect-choice-existing-owner-v1"
        record = SimpleNamespace(
            name="Optional Probe",
            type_line="Enchantment",
            oracle_text="",
            faces=(),
        )
        ability = {"face_id": "front"}
        accepted = (
            "Whenever you cast a noncreature spell, you may gain 2 life.",
            "Whenever a land you control enters, you may draw a card.",
        )
        rejected = (
            "You may destroy target artifact.",
            "{2}, {T}: You may create a Treasure token.",
            "Whenever equipped creature attacks, you may draw a card.",
            "You may pay {1}. If you do, draw a card.",
            "You may choose target creature.",
            "You may destroy target artifact if you control a Wizard.",
        )

        for source in accepted:
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )
        for source in rejected:
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )

    def test_typed_public_event_probe_uses_integrated_carrier_and_body(self):
        probe_id = "typed-public-event-effect-trigger-existing-owner-v1"
        record = SimpleNamespace(
            name="Public Event Probe",
            type_line="Enchantment",
            oracle_text="",
            faces=(),
        )
        ability = {"face_id": "front"}
        accepted = (
            "Whenever a creature attacks, you may gain 1 life.",
            "Whenever an opponent casts a blue spell during your turn, you "
            "may create a 4/4 green Elemental creature token.",
        )
        rejected = (
            "Whenever an opponent discards a card, you may draw a card.",
            "Whenever you sacrifice a creature, you may gain 1 life.",
            "Whenever equipped creature attacks, you may gain 1 life.",
            "Whenever one or more creatures attack, you may gain 1 life.",
            "Whenever a creature attacks, you may choose a card name.",
        )
        for source in accepted:
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )
        for source in rejected:
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )

    def test_fixed_optional_mana_payment_probe_uses_integrated_trigger_boundary(self):
        probe_id = "fixed-optional-mana-payment-trigger-existing-owner-v1"
        record = SimpleNamespace(
            name="Payment Probe",
            type_line="Creature — Fixture",
            oracle_text="",
            faces=(),
        )
        ability = {"face_id": "front"}
        accepted = (
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "draw a card.",
            "At the beginning of your upkeep, you may pay {2}. If you do, "
            "gain 2 life.",
            "When this creature enters, you may pay {1}. If you do, create a "
            "Treasure token.",
        )
        rejected = (
            "Whenever you cast a creature spell, you may pay {X}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G}. When you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "you may draw a card.",
        )

        for source in accepted:
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )
        for source in rejected:
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )

    def test_fixed_battlefield_query_probe_uses_integrated_characteristic_owner(self):
        probe_id = "fixed-battlefield-query-characteristics-existing-owner-v1"
        accepted = (
            "All Sliver creatures get +1/+1.",
            "Attacking creatures get +1/+1.",
            "Creatures your opponents control get -1/-0.",
            "Multicolored creatures you control have flying.",
            "Zombie tokens you control have hexproof and menace.",
            "Each creature you control with a +1/+1 counter on it has trample.",
        )
        rejected = (
            "Creatures you control get +1/+1 for each artifact you control.",
            "Creatures you control lose flying.",
            "Creatures you control have ward {2}.",
            "Creatures you control are blue in addition to their other colors.",
        )
        for source in accepted:
            with self.subTest(source=source):
                self.assertTrue(_matches_probe(probe_id, source))
        for source in rejected:
            with self.subTest(source=source):
                self.assertFalse(_matches_probe(probe_id, source))

    def test_public_state_characteristic_probe_selects_only_new_typed_grammar(self):
        accepted = (
            "Attacking creatures you control get +1/+1.",
            "Untapped creatures you control get +1/+1.",
            "Modified creatures you control have trample.",
            "This creature has indestructible as long as it is tapped.",
            "As long as this creature is equipped, it gets +2/+2 and has flying.",
            "As long as enchanted creature is black, it gets +1/+1.",
            "Artifacts you control have shroud as long as you control "
            "three or more artifacts.",
        )
        rejected = (
            "Creatures you control get +1/+1.",
            "Creatures you control get +1/+1 for each artifact you control.",
            "Creatures you control have flying as long as you control "
            "an artifact with flying.",
            "Artifacts you control have shroud as long as you control "
            "three artifacts.",
            "Artifacts you control have shroud as long as an opponent "
            "controls two or more artifacts.",
            "Attacking tapped creatures you control have flying.",
            "Attacking creatures you control with a +1/+1 counter on them "
            "have trample.",
            "Creatures you control are blue in addition to their other colors.",
            'During your turn, this creature has "{T}: Draw a card."',
        )
        for source in accepted:
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_typed_public_state_characteristic_query(
                        source,
                        source_name="Selector Fixture",
                    )
                )
        for source in rejected:
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_typed_public_state_characteristic_query(
                        source,
                        source_name="Selector Fixture",
                    )
                )

    def test_fixed_regeneration_probe_uses_closed_contextual_owners(self):
        probe_id = "fixed-regeneration-existing-owner-v1"
        spell = SimpleNamespace(
            name="Death Ward",
            type_line="Instant",
            oracle_text="Regenerate target creature.",
            faces=(),
        )
        aura = SimpleNamespace(
            name="Gaea's Embrace",
            type_line="Enchantment — Aura",
            oracle_text="{G}: Regenerate enchanted creature.",
            faces=(),
        )
        creature = SimpleNamespace(
            name="Cromat",
            type_line="Legendary Creature — Illusion",
            oracle_text="{B}{G}: Regenerate Cromat.",
            faces=(),
        )
        ability = {"face_id": "front"}
        for record, source in (
            (spell, spell.oracle_text),
            (aura, aura.oracle_text),
            (creature, creature.oracle_text),
            (
                spell,
                "Destroy target creature. It can't be regenerated.",
            ),
            (
                spell,
                "Destroy all creatures. They can't be regenerated.",
            ),
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )
        for source in (
            "Regenerate target Elf.",
            "Regenerate two target creatures.",
            "Destroy target creature. It can't be regenerated if it attacked.",
            "Destroy target creature. Draw a card for each creature destroyed this way.",
        ):
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=spell,
                        ability=ability,
                    )
                )

    def test_fixed_library_selection_probe_uses_complete_partition_grammar(self):
        probe_id = "fixed-library-selection-existing-owner-v1"
        accepted = (
            "Look at the top three cards of your library. Put one of them "
            "into your hand and the rest into your graveyard.",
            "{2}, {T}: Look at the top three cards of your library. You may "
            "reveal a creature card from among them and put it into your hand. "
            "Put the rest on the bottom of your library in any order.",
            "When this artifact enters, reveal the top two cards of your "
            "library. Put all land cards revealed this way into your hand and "
            "the rest into your graveyard.",
            "• Look at the top four cards of your library. Put up to two "
            "instant and/or sorcery cards from among them into your hand and "
            "the rest into your graveyard.",
        )
        rejected = (
            "Look at the top X cards of your library. Put one into your hand "
            "and the rest into your graveyard.",
            "Look at the top three cards of target player's library. Put one "
            "into your hand and the rest into their graveyard.",
            "Look at the top three cards of your library. Put a creature card "
            "with mana value 2 or less into your hand and the rest into your "
            "graveyard.",
            "Look at the top three cards of your library. Put one into your "
            "hand and the rest into your graveyard. You gain 3 life.",
        )
        for source in accepted:
            with self.subTest(source=source):
                self.assertTrue(_matches_probe(probe_id, source))
        for source in rejected:
            with self.subTest(source=source):
                self.assertFalse(_matches_probe(probe_id, source))

    def test_spell_cast_characteristic_probe_uses_closed_event_and_body(self):
        probe_id = (
            "fixed-spell-cast-characteristic-trigger-existing-owner-v2"
        )
        record = SimpleNamespace(
            name="Characteristic trigger source",
            type_line="Artifact",
            oracle_text="",
            faces=(),
        )
        ability = {"face_id": "front"}
        for source in (
            "Whenever you cast a white spell, draw a card.",
            "Whenever a player casts a Spirit or Arcane spell, you gain 1 life.",
            "Whenever an opponent casts a colorless or multicolored spell, scry 1.",
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )
        for source in (
            "Whenever you cast a spell with mana value 3, draw a card.",
            "Whenever you cast a historic spell, draw a card.",
            "Whenever you cast a Spirit spell, perform an unsupported action.",
            (
                "Whenever you cast a Spirit or Arcane spell, regenerate "
                "target creature."
            ),
            (
                "Whenever you cast a Spirit or Arcane spell, you may return "
                "Characteristic trigger source to its owner's hand."
            ),
        ):
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )

    def test_typed_spell_cast_fact_probe_uses_extended_event_and_body(self):
        probe_id = "typed-spell-cast-fact-predicate-existing-owner-v1"
        record = SimpleNamespace(
            name="Extended cast trigger source",
            type_line="Artifact",
            oracle_text="",
            faces=(),
        )
        ability = {"face_id": "front"}
        for source in (
            "When you cast this spell, draw a card.",
            "Whenever you cast your second spell each turn, you gain 1 life.",
            "Whenever you cast a historic spell, scry 1.",
            (
                "Whenever you cast a historic spell, draw a card. "
                "(Artifacts, legendaries, and Sagas are historic.)"
            ),
            "Whenever you cast a green permanent spell, draw a card.",
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )
        for source in (
            "Whenever you cast a white spell, draw a card.",
            "Whenever you cast a spell with mana value X, draw a card.",
            "Whenever you cast your second spell each turn, do the impossible.",
            "Whenever you cast or copy a spell, draw a card.",
        ):
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )

    def test_query_characteristic_probes_preserve_completed_grammar_identity(self):
        old_probe = "typed-query-self-characteristic-existing-owner-v1"
        gated_probe = "query-gated-self-characteristic-existing-owner-v1"
        old_sources = (
            "This creature gets +1/+1 for each artifact you control.",
            "This creature gets +2/+2 as long as you control three or more "
            "artifacts.",
        )
        gated_sources = (
            "As long as you control an artifact, this creature gets +2/+0 "
            "and has flying.",
            "This creature has flying as long as you control an artifact.",
        )
        for source in old_sources:
            with self.subTest(source=source, probe=old_probe):
                self.assertTrue(
                    _matches_query_self_characteristic_probe(
                        old_probe,
                        source,
                        source_name="Probe Source",
                    )
                )
                self.assertFalse(
                    _matches_query_self_characteristic_probe(
                        gated_probe,
                        source,
                        source_name="Probe Source",
                    )
                )
        for source in gated_sources:
            with self.subTest(source=source, probe=gated_probe):
                self.assertFalse(
                    _matches_query_self_characteristic_probe(
                        old_probe,
                        source,
                        source_name="Probe Source",
                    )
                )
                self.assertTrue(
                    _matches_query_self_characteristic_probe(
                        gated_probe,
                        source,
                        source_name="Probe Source",
                    )
                )

    def test_source_pronoun_damage_trigger_probe_is_bounded_and_contextual(self):
        probe_id = "fixed-source-pronoun-damage-trigger-existing-owner-v1"
        record = SimpleNamespace(
            name="Damage trigger source",
            type_line="Creature — Archer",
            oracle_text="",
            faces=(),
        )
        ability = {"face_id": "front"}
        for source in (
            "When this creature enters, it deals 1 damage to any target.",
            "When Damage trigger source dies, it deals 2 damage to target "
            "creature.",
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )
        for source in (
            "When this creature leaves the battlefield, it deals 1 damage "
            "to any target.",
            "When this creature enters, it deals X damage to any target.",
            "When this creature dies, it deals 2 damage to any target and "
            "you gain 2 life.",
            "Whenever another creature enters, it deals 1 damage to any target.",
        ):
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )

    def test_current_work_selection_policy_version_is_supported(self):
        queue = build_rules_dependency_queue_from_root(ROOT)

        self.assertEqual(11, queue["work_selection"]["policy_version"])

    def test_fixed_face_down_lifecycle_probe_is_closed(self):
        probe_id = "fixed-face-down-lifecycle-existing-owner-v1"
        for source in (
            "Disguise {4}{W} (You may cast this card face down for {3}.)",
            "Megamorph {5}{G}",
            "When this creature is turned face up, draw a card.",
            "Whenever this Equipment is turned face up, attach it to target "
            "creature you control.",
        ):
            with self.subTest(source=source):
                self.assertTrue(_matches_probe(probe_id, source))
        for source in (
            "Morph {3}",
            "Disguise {W/U}",
            "Megamorph {X}{G}",
            "Whenever a permanent is turned face up, draw a card.",
            "When another creature is turned face up, draw a card.",
            "When this creature turns face up, draw a card.",
        ):
            with self.subTest(source=source):
                self.assertFalse(_matches_probe(probe_id, source))

    def test_fixed_casting_surface_probe_is_closed(self):
        probe_id = "fixed-casting-surface-existing-owner-v2"
        for source in (
            "Buyback {3}",
            "Dash {1}{R}",
            "Warp {2}{U}",
            "Retrace",
            "Creature spells with flying you cast cost {1} less to cast.",
            "Artifact spells your opponents cast cost {2} more to cast.",
            "The first creature spell you cast each turn costs {1} less to cast.",
        ):
            with self.subTest(source=source):
                self.assertTrue(_matches_probe(probe_id, source))
        for source in (
            "Blitz {1}{R}",
            "Buyback—Discard a card.",
            "Dash {X}{R}",
            "Warp {W/U}",
            "Retrace—Discard two lands.",
            "Spells of the chosen type cost {1} less to cast.",
            "This spell costs {1} less to cast if it targets a creature.",
        ):
            with self.subTest(source=source):
                self.assertFalse(_matches_probe(probe_id, source))

    def test_fixed_activation_zone_change_predicate_probe_is_closed(self):
        probe_id = (
            "fixed-activation-zone-change-predicates-existing-owner-v2"
        )
        record = SimpleNamespace(
            name="Fixed Cost Probe",
            type_line="Creature — Goblin",
            oracle_text="",
            keywords=(),
            faces=(),
        )
        ability = {"face_id": "front"}
        for source in (
            "{1}, {T}, Sacrifice another creature or artifact: Draw a card.",
            "Sacrifice an Eldrazi Scion: Draw a card.",
            "Sacrifice a Caribou token: Draw a card.",
            "Sacrifice another black creature: Draw a card.",
            "Sacrifice another Vampire or Zombie: Draw a card.",
            "Sacrifice an artifact token: Draw a card.",
            "Sacrifice a snow Mountain: Draw a card.",
            "Sacrifice a nonland permanent: Draw a card.",
            "Sacrifice a noncreature artifact: Draw a card.",
            "Sacrifice an artifact creature: Draw a card.",
            "Sacrifice a Forest or Plains: Draw a card.",
            "Exile a card from your graveyard: Draw a card.",
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )
        for source in (
            "Sacrifice two Goblins: Draw a card.",
            "Sacrifice another creature or a Treasure: Draw a card.",
            "Sacrifice an artifact or another creature: Draw a card.",
            "Sacrifice another creature or token: Draw a card.",
            "Sacrifice another creature or Vehicle: Draw a card.",
            "Sacrifice a creature with defender: Draw a card.",
            "Sacrifice a modified creature: Draw a card.",
            "Sacrifice this creature, Sacrifice another creature: Draw a card.",
            "{W/U}, Sacrifice another creature: Draw a card.",
            "Sacrifice another creature: Add {B}.",
        ):
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=record,
                        ability=ability,
                    )
                )

    def test_ordinary_saga_chapter_program_probe_is_closed(self):
        probe_id = "ordinary-saga-chapter-programs-existing-owner-v1"
        saga = SimpleNamespace(
            name="Ordinary Saga Probe",
            type_line="Enchantment — Saga",
            oracle_text="I — Draw a card.",
            keywords=(),
            faces=(),
        )
        ability = {"face_id": "front"}
        for source in (
            "I — Draw a card.",
            "II, III — You gain 2 life.",
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=saga,
                        ability=ability,
                    )
                )
        for source in (
            "III, II — Draw a card.",
            "I, I — Draw a card.",
            "XI — Draw a card.",
            "I —",
        ):
            with self.subTest(source=source):
                self.assertFalse(
                    _matches_probe(
                        probe_id,
                        source,
                        card_record=saga,
                        ability=ability,
                    )
                )
        non_saga = SimpleNamespace(
            **{**saga.__dict__, "type_line": "Enchantment"}
        )
        self.assertFalse(
            _matches_probe(
                probe_id,
                "I — Draw a card.",
                card_record=non_saga,
                ability=ability,
            )
        )

    def test_self_spell_cost_reduction_probe_is_closed(self):
        probe_id = "fixed-self-spell-cost-reduction-existing-owner-v1"
        for source in (
            "This spell costs {2} less to cast if you control a Wizard.",
            "This spell costs {1} less to cast for each creature card in your graveyard.",
            "This spell costs {X} less to cast, where X is the total mana value of noncreature artifacts you control.",
            "This spell costs {3} less to cast if a creature died this turn.",
            "Domain — This spell costs {1} less to cast for each basic land type among lands you control.",
            "This spell costs {G} less to cast for each green creature you control.",
        ):
            with self.subTest(source=source):
                self.assertTrue(_matches_probe(probe_id, source))
        for source in (
            "This spell costs {2} less to cast if it targets a tapped creature.",
            "This spell costs {1} less to cast for each card you've drawn this turn.",
            "This spell costs {X} less to cast, where X is the total power of creatures you control.",
            "This spell costs {1} less to cast for each card with an Adventure in your graveyard.",
            "The first spell you cast each turn costs {1} less to cast.",
        ):
            with self.subTest(source=source):
                self.assertFalse(_matches_probe(probe_id, source))

    def test_spell_history_transformation_probe_is_closed_and_accounted(self):
        probe_id = "spell-history-transformations-existing-owner-v1"
        for source in (
            "Daybound (If a player casts no spells, it becomes night.)",
            "Nightbound",
            "At the beginning of each upkeep, if no spells were cast last "
            "turn, transform this creature.",
            "At the beginning of each upkeep, if a player cast two or more "
            "spells last turn, transform Arin.",
        ):
            with self.subTest(source=source):
                self.assertTrue(_matches_probe(probe_id, source))
        for source in (
            "At the beginning of your upkeep, if no spells were cast last "
            "turn, transform this creature.",
            "At the beginning of each upkeep, if a player cast three or more "
            "spells last turn, transform this creature.",
            "It becomes night.",
            "Convert this creature.",
        ):
            with self.subTest(source=source):
                self.assertFalse(_matches_probe(probe_id, source))

        record = SimpleNamespace(
            oracle_id="fixture:spell-history-transform",
            name="Day Face // Night Face",
            oracle_text="Daybound",
            faces=(),
        )
        frontier = {
            "cards": [
                {
                    "oracle_id": record.oracle_id,
                    "exact_ability_count": 1,
                    "abilities": [
                        {
                            "face_id": "front",
                            "source_line": 1,
                            "status": "residual",
                        }
                    ],
                }
            ]
        }
        compiled = SimpleNamespace(
            status="exact",
            material_residuals=(),
            faces=(
                SimpleNamespace(
                    nodes=(
                        SimpleNamespace(
                            exact=True,
                            template_id="daybound-static-v1",
                        ),
                    )
                ),
            ),
        )
        with (
            mock.patch(
                "scripts.work_selection_cohort_measurements."
                "load_default_capability_registry",
                return_value=object(),
            ),
            mock.patch(
                "scripts.work_selection_cohort_measurements.compile_oracle_card",
                return_value=compiled,
            ),
        ):
            measurement = _spell_history_transformation_measurement(
                frontier=frontier,
                bundle_id="bundle:spell-history-transformations",
                probe_id=probe_id,
                cards_by_oracle_id={record.oracle_id: record},
                coverage={
                    "minimum_complete_card_gain": 1,
                    "minimum_exact_ability_gain": 1,
                    "minimum_material_residual_reduction": 1,
                },
                cohort_fingerprint="0" * 64,
            )
        self.assertEqual("bounded_executable", measurement["decision"])
        self.assertEqual(1, measurement["affected_commander_cards"])
        self.assertEqual(1, measurement["complete_card_gain"])
        self.assertEqual(1, measurement["exact_ability_gain"])
        self.assertEqual(1, measurement["material_residual_reduction"])

    def test_attached_quoted_grant_probe_is_integrated_and_accounted(self):
        outcome = next(
            row
            for row in self.work_inputs["harvest_outcome_history"]["entries"]
            if row.get("transition_id")
            == "oracle-ir-v156-attached-quoted-ability-grants"
        )
        coverage = self.catalog["work_selection"]["coverage_family"]
        self.assertEqual(
            "measurement:attached-quoted-ability-grants",
            outcome["measurement_id"],
        )
        self.assertEqual(
            "attached-quoted-ability-grant-existing-owner-v1",
            outcome["measurement_probe_id"],
        )
        self.assertGreaterEqual(
            outcome["actual_complete_card_gain"],
            coverage["minimum_complete_card_gain"],
        )
        self.assertEqual(
            outcome["actual_complete_card_gain"],
            outcome["actual_trusted_card_gain"],
        )
        self.assertEqual(
            outcome["actual_complete_card_gain"],
            outcome["actual_capability_closed_card_gain"],
        )
        self.assertEqual(
            outcome["oracle_exact_ability_node_delta"],
            2 * outcome["frontier_ability_carrier_delta"]["additions"],
        )
        self.assertEqual(
            outcome["actual_material_oracle_residual_reduction"],
            outcome["actual_material_card_program_residual_reduction"],
        )
        self.assertEqual(
            outcome["actual_material_residual_reduction"],
            outcome["actual_material_oracle_residual_reduction"],
        )
        assurance = outcome["interaction_assurance_delta"]
        self.assertGreater(assurance["applicable_high_risk_pairs"], 0)
        self.assertEqual(
            assurance["applicable_high_risk_pairs"],
            assurance["covered_high_risk_pairs"],
        )

    def test_attached_grant_probe_derives_high_risk_capability_pairs(self):
        source = 'Equipped creature has "{2}, {T}: Target player mills three cards."'
        record = SimpleNamespace(
            oracle_id="fixture:attached-grant-risk",
            name="Attached grant risk fixture",
            oracle_text=source,
            faces=(),
        )
        outer = SimpleNamespace(
            exact=True,
            kind="static_ability",
            template_id=(
                "continuous-attached-fixed-characteristics-"
                "granted-ability-v1"
            ),
            span=SimpleNamespace(line=1),
            capability_dependencies=("attachment.equip.fixed_mana",),
        )
        inner = SimpleNamespace(
            exact=True,
            kind="granted_activated_ability",
            template_id="mill-fixed-target-any-v1",
            span=SimpleNamespace(line=1),
            capability_dependencies=("zone.mill.fixed",),
        )
        compiled = SimpleNamespace(
            faces=(SimpleNamespace(face_id="front", nodes=(outer, inner)),),
            material_residuals=(),
            status="exact",
        )
        frontier = {
            "cards": [
                {
                    "oracle_id": record.oracle_id,
                    "abilities": [
                        {
                            "ability_id": "front:n1",
                            "face_id": "front",
                            "source_line": 1,
                            "status": "unresolved",
                            "residuals": [{"residual_id": "r1"}],
                        }
                    ],
                }
            ]
        }
        coverage = {
            "minimum_complete_card_gain": 1,
            "minimum_exact_ability_gain": 1,
            "minimum_material_residual_reduction": 1,
        }
        with mock.patch(
            "scripts.work_selection_cohort_measurements.compile_oracle_card",
            return_value=compiled,
        ):
            measurement = _attached_quoted_ability_grant_measurement(
                frontier=frontier,
                bundle_id="bundle:attached-quoted-ability-grants",
                probe_id="attached-quoted-ability-grant-existing-owner-v1",
                cards_by_oracle_id={record.oracle_id: record},
                coverage=coverage,
                cohort_fingerprint="fixture-fingerprint",
            )
        self.assertEqual(
            1,
            measurement["candidate_accounting"][
                "newly_applicable_high_risk_pairs"
            ],
        )

    def test_source_combat_growth_probe_requires_integrated_exact_node(self):
        source = (
            "Whenever this creature attacks, it gets +2/+0 until end of turn."
        )
        record = SimpleNamespace(
            oracle_id="fixture:source-combat-growth",
            name="Source combat growth fixture",
            oracle_text=source,
            type_line="Creature — Test",
            faces=(),
        )
        ability = {
            "ability_id": "front:n1",
            "face_id": "front",
            "source_line": 1,
            "status": "unresolved",
            "residuals": [{"residual_id": "r1"}],
        }
        frontier = {
            "cards": [
                {
                    "oracle_id": record.oracle_id,
                    "abilities": [ability],
                }
            ]
        }
        node = SimpleNamespace(
            exact=True,
            span=SimpleNamespace(line=1),
            effects=(
                {
                    "op": "modify_stats_until_end_of_turn",
                    "card": "$source.zone_object",
                    "power": 2,
                    "toughness": 0,
                },
            ),
            runtime_coverage=("current_ability_fragment_required",),
        )
        compiled = SimpleNamespace(
            faces=(SimpleNamespace(face_id="front", nodes=(node,)),),
            material_residuals=(),
            status="exact",
        )
        with (
            mock.patch(
                "scripts.work_selection_cohort_measurements."
                "load_default_capability_registry",
                return_value=object(),
            ),
            mock.patch(
                "scripts.work_selection_cohort_measurements."
                "compile_oracle_card",
                return_value=compiled,
            ),
        ):
            measurement = _source_combat_growth_trigger_measurement(
                frontier=frontier,
                bundle_id="bundle:fixed-source-combat-growth-triggers",
                probe_id=(
                    "fixed-source-combat-growth-trigger-existing-owner-v1"
                ),
                cards_by_oracle_id={record.oracle_id: record},
                coverage={
                    "minimum_complete_card_gain": 1,
                    "minimum_exact_ability_gain": 1,
                    "minimum_material_residual_reduction": 1,
                },
                cohort_fingerprint="0" * 64,
            )
        self.assertEqual("bounded_executable", measurement["decision"])
        self.assertEqual(1, measurement["affected_commander_cards"])
        self.assertEqual(1, measurement["complete_card_gain"])
        self.assertEqual(1, measurement["exact_ability_gain"])
        self.assertEqual(1, measurement["material_residual_reduction"])
        self.assertEqual(
            1,
            measurement["candidate_accounting"][
                "affected_oracle_carriers"
            ],
        )

    def test_entry_return_probe_requires_integrated_exact_node(self):
        source = (
            "When this land enters, return a land you control to its owner's hand."
        )
        record = SimpleNamespace(
            oracle_id="fixture:entry-return",
            name="Entry return fixture",
            oracle_text=source,
            type_line="Land",
            faces=(),
        )
        ability = {
            "ability_id": "front:n1",
            "face_id": "front",
            "source_line": 1,
            "status": "unresolved",
            "residuals": [{"residual_id": "r1"}],
        }
        frontier = {
            "cards": [
                {
                    "oracle_id": record.oracle_id,
                    "abilities": [ability],
                }
            ]
        }
        node = SimpleNamespace(
            exact=True,
            span=SimpleNamespace(line=1),
            template_id=(
                "fixed-typed-effect-entry-return-public-zone-trigger-v1"
            ),
            effects=(
                {
                    "op": "choose_cards_apnap",
                    "actor": "$controller",
                },
            ),
            runtime_coverage=("current_ability_fragment_required",),
            capability_dependencies=(
                "choice.controller.fixed_return_owner_hand",
            ),
        )
        compiled = SimpleNamespace(
            faces=(SimpleNamespace(face_id="front", nodes=(node,)),),
            material_residuals=(),
            status="exact",
        )
        with (
            mock.patch(
                "scripts.work_selection_cohort_measurements."
                "load_default_capability_registry",
                return_value=object(),
            ),
            mock.patch(
                "scripts.work_selection_cohort_measurements."
                "compile_oracle_card",
                return_value=compiled,
            ),
        ):
            measurement = _fixed_entry_return_requirement_measurement(
                frontier=frontier,
                bundle_id="bundle:fixed-entry-return-requirements",
                probe_id=(
                    "fixed-entry-return-requirement-existing-owner-v1"
                ),
                cards_by_oracle_id={record.oracle_id: record},
                coverage={
                    "minimum_complete_card_gain": 1,
                    "minimum_exact_ability_gain": 1,
                    "minimum_material_residual_reduction": 1,
                },
                cohort_fingerprint="0" * 64,
            )
        self.assertEqual("bounded_executable", measurement["decision"])
        self.assertEqual(1, measurement["affected_commander_cards"])
        self.assertEqual(1, measurement["complete_card_gain"])
        self.assertEqual(1, measurement["exact_ability_gain"])
        self.assertEqual(1, measurement["material_residual_reduction"])

    def test_fixed_activation_measurement_counts_only_exact_compiled_nodes(self):
        member_ids = {"activated_cost:fixed-zone-change"}
        frontier = {
            "cards": [
                {
                    "oracle_id": oracle_id,
                    "minimum_known_blocker_set": list(member_ids),
                    "abilities": [
                        {
                            "ability_id": "n1",
                            "source_line": 1,
                            "status": (
                                "exact"
                                if oracle_id == "already-exact"
                                else "residual"
                            ),
                            "blockers": {
                                "canonical_family_ids": list(member_ids)
                            },
                        },
                        *(
                            [
                                {
                                    "ability_id": "n1",
                                    "source_line": 1,
                                    "blockers": {
                                        "canonical_family_ids": list(member_ids)
                                    },
                                }
                            ]
                            if oracle_id == "exact"
                            else []
                        ),
                    ],
                }
                for oracle_id in ("exact", "residual", "already-exact")
            ]
        }
        records = {
            oracle_id: SimpleNamespace(
                name=oracle_id,
                oracle_text="Sacrifice another creature: Draw a card.",
                faces=(),
            )
            for oracle_id in ("exact", "residual", "already-exact")
        }

        def compiled(record, **_kwargs):
            exact = record.name != "residual"
            return SimpleNamespace(
                status="exact" if exact else "partial",
                faces=(
                    SimpleNamespace(
                        nodes=(
                            SimpleNamespace(
                                node_id="n1",
                                exact=exact,
                                kind="activated_ability",
                            ),
                        )
                    ),
                ),
            )

        with (
            mock.patch(
                "scripts.work_selection_cohort_measurements._matches_probe",
                return_value=True,
            ),
            mock.patch(
                "scripts.work_selection_cohort_measurements.compile_oracle_card",
                side_effect=compiled,
            ),
            mock.patch(
                "scripts.work_selection_cohort_measurements."
                "load_default_capability_registry",
                return_value=object(),
            ),
        ):
            measured = _fixed_activation_zone_change_predicate_measurement(
                frontier=frontier,
                bundle_id="bundle:fixed-activation-zone-change-predicates",
                probe_id=(
                    "fixed-activation-zone-change-predicates-existing-owner-v2"
                ),
                member_ids=member_ids,
                cards_by_oracle_id=records,
                coverage={
                    "minimum_complete_card_gain": 1,
                    "minimum_exact_ability_gain": 1,
                    "minimum_material_residual_reduction": 1,
                },
                cohort_fingerprint="0" * 64,
            )

        self.assertEqual(1, measured["affected_commander_cards"])
        self.assertEqual(1, measured["complete_card_gain"])
        self.assertEqual(1, measured["exact_ability_gain"])
        self.assertEqual(1, measured["material_residual_reduction"])

    def test_trigger_ability_word_carrier_probe_is_closed(self):
        probe_id = "trigger-ability-word-carrier-existing-owner-v1"
        for source in (
            "Keen Senses — When this creature enters, draw a card.",
            "Alliance — Whenever another creature you control enters, scry 1.",
            "Combat Inspiration — At the beginning of combat on your turn, "
            "target creature gets +1/+0 until end of turn.",
        ):
            with self.subTest(source=source):
                self.assertTrue(_matches_probe(probe_id, source))
        for source in (
            "Threshold — As long as seven cards are in your graveyard, this "
            "creature gets +1/+1.",
            "I — Draw a card.",
            'This creature has "Heroic — Whenever you cast a spell, draw a '
            'card."',
            "Whenever another creature you control enters, scry 1.",
        ):
            with self.subTest(source=source):
                self.assertFalse(_matches_probe(probe_id, source))

    def test_current_transition_measurement_is_generated_not_policy_counted(self):
        work_selection = self.catalog["work_selection"]
        declaration = work_selection["semantic_transition_declaration"]
        if declaration["bundle_id"] is None:
            self.assertIsNone(declaration["expected_complete_card_gain"])
            self.assertTrue(declaration["non_harvest_reason"])
            self.assertEqual(
                [],
                self.work_inputs["cohort_measurements"][
                    "transition_measurements"
                ],
            )
            pending = self.work_inputs["harvest_outcome_history"][
                "pending_transition"
            ]
            self.assertEqual("non_harvest", pending["outcome_kind"])
            self.assertEqual(
                declaration["transition_id"], pending["transition_id"]
            )
            return
        bundle = next(
            row
            for row in work_selection["coverage_family"]["candidate_bundles"]
            if row["bundle_id"] == declaration["bundle_id"]
        )

        self.assertRegex(declaration["measurement_id"], r"^measurement:")
        self.assertNotIn("expected_complete_card_gain", declaration)
        self.assertEqual("generated_probe", bundle["measurement_status"])
        self.assertFalse(
            any(field.startswith("frontier_") for field in bundle)
        )
        transition_measurement = next(
            row
            for row in self.work_inputs["cohort_measurements"][
                "transition_measurements"
            ]
            if row["transition_id"] == declaration["transition_id"]
        )
        measurement = transition_measurement["measurement"]
        self.assertEqual(
            declaration["measurement_id"], measurement["measurement_id"]
        )
        self.assertEqual(bundle["bundle_id"], measurement["bundle_id"])
        if bundle["measurement_probe_id"] != measurement["probe_id"]:
            current = next(
                row
                for row in self.work_inputs["harvest_outcome_history"]["entries"]
                if row.get("transition_id") == declaration["transition_id"]
            )
            self.assertEqual(
                bundle["measurement_probe_id"],
                current["forecast_correction"]["measurement_probe_id"],
            )
        coverage = work_selection["coverage_family"]
        self.assertGreater(measurement["complete_card_gain"], 0)
        self.assertTrue(
            measurement["complete_card_gain"]
            >= coverage["minimum_complete_card_gain"]
            or measurement["exact_ability_gain"]
            >= coverage["minimum_exact_ability_gain"]
            or measurement["material_residual_reduction"]
            >= coverage["minimum_material_residual_reduction"]
        )
        self.assertGreater(measurement["exact_ability_gain"], 0)

    def test_stale_generated_measurement_fails_before_selection(self):
        inputs = _without_pending_harvest_transition(self.work_inputs)
        token = next(
            row
            for row in inputs["cohort_measurements"]["measurements"]
            if row["bundle_id"] == "bundle:fixed-token-creation-contexts"
        )
        token["cohort_fingerprint"] = "0" * 64
        _refingerprint(inputs["cohort_measurements"])

        with self.assertRaisesRegex(WorkSelectionError, "identity or metric"):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=self.catalog["work_selection"],
                inputs=inputs,
            )

    def test_measurement_freshness_ignores_unrelated_frontier_churn(self):
        inputs = _without_pending_harvest_transition(self.work_inputs)
        frontier = inputs["card_unlock_frontier"]
        frontier["fingerprint"] = "f" * 64
        measured_family_ids = {
            family_id
            for bundle in self.catalog["work_selection"]["coverage_family"][
                "candidate_bundles"
            ]
            if bundle["measurement_probe_id"] is not None
            for family_id in bundle["member_family_ids"]
        }
        unrelated = next(
            row
            for row in frontier["family_candidates"]
            if row["family_id"] not in measured_family_ids
        )
        unrelated["affected_cards"] += 1
        inputs["cohort_measurements"]["frontier_fingerprint"] = "f" * 64
        _refingerprint(inputs["cohort_measurements"])

        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=inputs,
        )

        self.assertIsNone(selected_work_candidate(work))
        self.assertEqual(0, work["eligible_candidate_count"])

    def test_relevant_frontier_change_requires_generated_remeasurement(self):
        inputs = _without_pending_harvest_transition(self.work_inputs)
        inputs["card_unlock_frontier"]["fingerprint"] = "e" * 64
        token_family = next(
            row
            for row in inputs["card_unlock_frontier"]["family_candidates"]
            if row["family_id"] == "effect_clause:create-token"
        )
        token_family["affected_cards"] += 1
        inputs["cohort_measurements"]["frontier_fingerprint"] = "e" * 64
        _refingerprint(inputs["cohort_measurements"])

        with self.assertRaisesRegex(WorkSelectionError, "identity or metric"):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=self.catalog["work_selection"],
                inputs=inputs,
            )

    def test_completed_bundle_retires_and_upper_bound_requires_bounded_cohort(self):
        work = build_work_selection(
            selected_batch=self.queue["selected_batch"],
            policy=self.catalog["work_selection"],
            inputs=self.work_inputs,
        )
        candidate_ids = {
            candidate["candidate_id"] for candidate in work["candidates"]
        }
        self.assertTrue(
            {
                "bundle:commander-pairing-keywords",
                "bundle:fixed-optional-effect-choices",
            }.isdisjoint(candidate_ids)
        )
        optional_measurement = next(
            row
            for row in self.work_inputs["cohort_measurements"]["measurements"]
            if row["bundle_id"] == "bundle:fixed-optional-effect-choices"
        )
        self.assertEqual(
            "retired_below_harvest_floor", optional_measurement["decision"]
        )
        coverage_policy = self.catalog["work_selection"]["coverage_family"]
        for metric, threshold in (
            ("complete_card_gain", "minimum_complete_card_gain"),
            ("exact_ability_gain", "minimum_exact_ability_gain"),
            (
                "material_residual_reduction",
                "minimum_material_residual_reduction",
            ),
        ):
            with self.subTest(metric=metric):
                self.assertLess(
                    optional_measurement[metric],
                    coverage_policy[threshold],
                )
        self.assertFalse(optional_measurement["grants_gameplay_trust"])

        frontier, policies, weights = _bounded_candidate_bundle_fixture()
        measurement = candidate_frontier_measurements(
            frontier, policies, weights, {}
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
            frontier, policies, weights, {}
        )[0]
        self.assertTrue(measurement["bounded_executable_verified"])

        frontier["family_candidates"][0][
            "lowerable_untrusted_abilities"
        ] = 0
        measurement = candidate_frontier_measurements(
            frontier, policies, weights, {}
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
            candidate_frontier_measurements(frontier, policies, weights, {}),
        )

    def test_partially_missing_candidate_bundle_remains_invalid(self):
        frontier, policies, weights = _bounded_candidate_bundle_fixture()
        frontier["family_candidates"].pop()

        with self.assertRaisesRegex(
            WorkSelectionBundleError,
            "references missing families: keyword_dependency:fixture-b",
        ):
            candidate_frontier_measurements(frontier, policies, weights, {})

        self.assertEqual(
            [],
            candidate_frontier_measurements(
                frontier,
                policies,
                weights,
                {},
                completed_bundle_ids={policies[0]["bundle_id"]},
            ),
        )

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
        inputs = _without_pending_harvest_transition(self.work_inputs)
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
        if selected is not None:
            self.assertFalse(selected["implementation_eligible"])
            self.assertEqual("cohort_measurement", selected["work_state"])
            self.assertFalse(
                selected["measurement_task"]["grants_gameplay_trust"]
            )
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

    def test_single_family_static_probe_policy_is_closed(self):
        policy = deepcopy(self.catalog["work_selection"])
        measured = next(
            row
            for row in policy["coverage_family"]["candidate_bundles"]
            if row["bundle_id"]
            == "bundle:fixed-public-state-characteristics"
        )
        bundles, _weights = validate_bundle_policy(
            policy["coverage_family"]
        )
        self.assertEqual(["static"], measured["source_contexts"])
        self.assertEqual(1, len(measured["member_family_ids"]))
        self.assertIn(measured, bundles)

        measured["member_family_ids"] = []
        with self.assertRaisesRegex(
            WorkSelectionBundleError, "closed identities"
        ):
            validate_bundle_policy(policy["coverage_family"])

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
            if row["measurement_status"] == "generated_probe"
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
            if row["measurement_status"] == "generated_probe"
        )
        measured["source_contexts"] = ["spell"]
        bundles, _weights = validate_bundle_policy(
            policy["coverage_family"]
        )
        self.assertIn(measured, bundles)

        inputs = deepcopy(self.work_inputs)
        measured = next(
            row
            for row in inputs["cohort_measurements"]["measurements"]
            if row["bundle_id"] == "bundle:fixed-token-creation-contexts"
        )
        measured["grants_gameplay_trust"] = True
        _refingerprint(inputs["cohort_measurements"])
        with self.assertRaisesRegex(
            WorkSelectionError, "identity or metric"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=self.catalog["work_selection"],
                inputs=inputs,
            )

        inputs = deepcopy(self.work_inputs)
        measured = next(
            row
            for row in inputs["cohort_measurements"]["measurements"]
            if row["bundle_id"] == "bundle:fixed-token-creation-contexts"
        )
        measured["exact_ability_gain"] = 100
        _refingerprint(inputs["cohort_measurements"])
        with self.assertRaisesRegex(
            WorkSelectionError, "contradicts the harvest floors"
        ):
            build_work_selection(
                selected_batch=self.queue["selected_batch"],
                policy=self.catalog["work_selection"],
                inputs=inputs,
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
