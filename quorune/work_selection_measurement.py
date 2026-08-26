from __future__ import annotations

from typing import Any, Mapping, Sequence


def cohort_measurement_spec(
    candidates: Sequence[Mapping[str, Any]],
    frontier: Mapping[str, Any],
) -> dict[str, Any] | None:
    options = [
        row
        for row in candidates
        if row["candidate_class"] == "compiler_harvest"
        and row["bundle"].get("measurement_status") == "upper_bound_only"
        and int(row.get("affected_commander_cards") or 0) > 0
    ]
    if not options:
        return None
    source = max(
        options,
        key=lambda row: (
            float(
                row["bundle"].get("predicted_complete_cards_per_cycle_hour") or 0
            ),
            float(
                row["bundle"].get("predicted_normalized_value_per_cycle_hour") or 0
            ),
            -int(row["bundle"].get("estimated_probe_hours") or 1),
            str(row["candidate_id"]),
        ),
    )
    source_bundle = dict(source["bundle"])
    source_bundle["bundle_id"] = "measurement:" + str(
        source_bundle["bundle_id"]
    ).split(":", 1)[-1]
    source_bundle["measurement_status"] = "cohort_measurement"
    measurement_id = str(source_bundle["bundle_id"])
    member_ids = [str(value) for value in source_bundle["member_family_ids"]]
    upper_bounds = {
        "affected_commander_cards": int(source.get("affected_commander_cards") or 0),
        "sole_blocker_cards": int(source.get("sole_blocker_cards") or 0),
        "one_additional_blocker_cards": int(
            source.get("one_additional_blocker_cards") or 0
        ),
        "two_additional_blocker_cards": int(
            source.get("two_additional_blocker_cards") or 0
        ),
        "exact_ability_upper_bound": int(
            source.get("expected_exact_ability_gain") or 0
        ),
        "material_residual_upper_bound": int(
            source.get("expected_material_residual_reduction") or 0
        ),
    }
    return {
        "candidate_id": measurement_id,
        "candidate_class": "compiler_harvest",
        "universal_subsystem": "bounded_cohort_measurement",
        "reusable_piece_ids": source.get("reusable_piece_ids", []),
        "rules_dependency_ids": source.get("rules_dependency_ids", []),
        "compiler_readiness": {
            "status": "measurement_only",
            "evidence": "the generated aggregate is upper-bound-only and not executable",
        },
        "runtime_readiness": {
            "status": "cohort_measurement",
            "evidence": "classify one exact grammar before implementation eligibility",
        },
        "assurance_readiness": {
            "status": "not_applicable_no_trust",
            "evidence": "measurement grants no gameplay trust or card support",
        },
        "affected_commander_cards": upper_bounds["affected_commander_cards"],
        "sole_blocker_cards": None,
        "one_additional_blocker_cards": None,
        "two_additional_blocker_cards": None,
        "expected_exact_ability_gain": None,
        "expected_complete_card_gain": None,
        "expected_material_residual_reduction": None,
        "interaction_debt_introduced": {"status": "not_applicable_no_trust"},
        "estimated_effort": "small",
        "reranking_reason": (
            "No executable cohort outranks this bounded measurement. Classify "
            f"{len(member_ids)} related frontier families without treating their "
            "aggregate upper bound as implementation authority."
        ),
        "eligible": True,
        "implementation_eligible": False,
        "work_state": "cohort_measurement",
        "measurement_task": {
            "source_corpus_filter": {
                "profile": str(frontier.get("profile") or "commander_review"),
                "frontier_fingerprint": str(frontier.get("fingerprint") or ""),
                "family_ids": member_ids,
                "source_contexts": list(source_bundle["source_contexts"]),
                "ability_statuses": ["unresolved"],
            },
            "owner_operation_hypothesis": {
                "canonical_owner_ids": list(source_bundle["canonical_owner_ids"]),
                "normalized_literal_parameters": list(
                    source_bundle["normalized_literal_parameters"]
                ),
            },
            "grammar_boundary": str(source_bundle["shared_grammar"]),
            "explicit_exclusions": list(source_bundle["explicit_exclusions"]),
            "cards_and_residuals_to_inspect": {
                "member_family_ids": member_ids,
                **upper_bounds,
            },
            "estimated_probe_hours": int(
                source_bundle.get("estimated_probe_hours") or 1
            ),
            "upgrade_evidence": [
                "enumerated Oracle-text cohort with one normalized grammar",
                "measured complete-card, exact-ability, and residual deltas",
                "one typed owner and operation boundary across every included context",
                "explicit rejection corpus for every excluded sibling grammar",
                "bounded executable census matching the declared cohort",
            ],
            "grants_gameplay_trust": False,
        },
        "bundle": source_bundle,
    }
