from __future__ import annotations

from typing import Any, Mapping, Sequence

from .work_selection_bundles import (
    atomic_frontier_bundle,
    bundle_measurement_decision,
    estimated_bundle_effort,
    single_candidate_bundle,
    validate_bundle_policy,
    validated_candidate_frontier_measurements,
    WorkSelectionBundleError,
)
from .work_selection_common import (
    WorkSelectionError,
    mapping as _mapping,
    nonnegative_int as _nonnegative_int,
    stable_hash as _hash,
)
from .work_selection_evidence import (
    load_work_selection_inputs,
    validate_harvest_history,
    work_selection_source_fingerprints,
)
from .work_selection_measurement import cohort_measurement_spec


WORK_SELECTION_SCHEMA_VERSION = 5
_CANDIDATE_CLASSES = {
    "ci_correctness",
    "replay_privacy_defect",
    "prohibited_runtime_semantics",
    "architecture_owner_or_mutation_defect",
    "interaction_assurance",
    "rules_foundation",
    "compiler_harvest",
    "card_family",
}
_REQUIRED_CANDIDATE_FIELDS = {
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
}
_REASON_FIELD = "reason"
_STATUS_FIELD = "status"
_BUNDLE_OUTPUT_FIELDS = {
    "bundle_id",
    "member_family_ids",
    "canonical_owner_ids",
    "source_contexts",
    "normalized_literal_parameters",
    "shared_dependencies",
    "shared_grammar",
    "estimated_implementation_hours",
    "estimated_probe_hours",
    "estimated_generation_hours",
    "estimated_cycle_hours",
    "predicted_complete_cards_per_cycle_hour",
    "predicted_normalized_value_per_cycle_hour",
    "expected_downstream_closure",
    "explicit_exclusions",
    "measurement_status",
    "synthesized",
}


def _validated_priority_policy(
    policy: Mapping[str, Any],
) -> tuple[list[str], int]:
    priority_classes = [str(value) for value in policy.get("priority_classes", [])]
    if (
        not priority_classes
        or len(priority_classes) != len(set(priority_classes))
        or set(priority_classes) != _CANDIDATE_CLASSES
    ):
        raise WorkSelectionError(
            "Work-selection priority classes must name every known class once"
        )
    assurance = _mapping(
        policy.get("interaction_assurance"), "interaction_assurance"
    )
    starting_uncovered = _nonnegative_int(
        assurance.get("starting_uncovered_high_risk_pairs"),
        "starting_uncovered_high_risk_pairs",
    )
    return priority_classes, starting_uncovered


def _validated_coverage_policy(coverage: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "minimum_complete_card_gain": 50,
        "minimum_exact_ability_gain": 100,
        "minimum_material_residual_reduction": 100,
        "minimum_prerequisite_complete_card_gain": 1,
        "minimum_prerequisite_downstream_card_gain": 50,
        "maximum_consecutive_prerequisite_exceptions": 1,
        "candidate_limit": 1,
    }
    values = {
        field: _nonnegative_int(coverage.get(field), field) for field in fields
    }
    rank_order = [str(value) for value in coverage.get("rank_order", [])]
    expected_rank_fields = {
        "expected_exact_ability_gain",
        "expected_complete_card_gain",
        "expected_material_residual_reduction",
    }
    invalid = (
        any(values[field] < minimum for field, minimum in fields.items())
        or values["minimum_prerequisite_complete_card_gain"]
        >= values["minimum_complete_card_gain"]
        or values["minimum_prerequisite_downstream_card_gain"]
        < values["minimum_complete_card_gain"]
        or len(rank_order) != len(set(rank_order))
        or set(rank_order) != expected_rank_fields
    )
    if invalid:
        raise WorkSelectionError(
            "Coverage work must declare the card, ability, residual, ranking, and candidate thresholds"
        )
    return {
        **values,
        "coverage_rank_order": rank_order,
        "excluded_efforts": {
            str(value) for value in coverage.get("excluded_efforts", []) if value
        },
    }


def _validated_prerequisite_exceptions(
    coverage: Mapping[str, Any], *, minimum_downstream_gain: int
) -> list[Mapping[str, Any]]:
    exceptions = list(coverage.get("approved_prerequisite_exceptions", []))
    ids: set[str] = set()
    expected = {
        "candidate_id",
        "expected_downstream_complete_card_gain",
        _REASON_FIELD,
    }
    for index, raw in enumerate(exceptions):
        row = _mapping(
            raw, f"approved_prerequisite_exceptions[{index}]"
        )
        if set(row) != expected:
            raise WorkSelectionError(
                "Approved prerequisite exceptions have an invalid shape"
            )
        candidate_id = str(row.get("candidate_id") or "")
        downstream_gain = _nonnegative_int(
            row.get("expected_downstream_complete_card_gain"),
            "expected_downstream_complete_card_gain",
        )
        reason = str(row.get(_REASON_FIELD) or "")
        if (
            not candidate_id
            or candidate_id in ids
            or downstream_gain < minimum_downstream_gain
            or not reason
        ):
            raise WorkSelectionError(
                "Approved prerequisite exceptions must be unique, measured, "
                "and complete"
            )
        ids.add(candidate_id)
    return exceptions


def _validated_candidate_bundles(
    coverage: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
    try:
        return validate_bundle_policy(coverage)
    except WorkSelectionBundleError as exc:
        raise WorkSelectionError(str(exc)) from exc


def _validated_harvest_history(
    value: Mapping[str, Any], *, minimum_gain: int
) -> dict[str, Any]:
    return validate_harvest_history(value, minimum_gain=minimum_gain)

def _validated_reviewed_history(
    policy: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    history = list(policy.get("reviewed_rerank_history", []))
    ids: set[str] = set()
    expected = {"candidate_id", "selected_over", _REASON_FIELD}
    for index, raw in enumerate(history):
        row = _mapping(raw, f"reviewed_rerank_history[{index}]")
        if set(row) != expected:
            raise WorkSelectionError(
                "Reviewed rerank history entries have an invalid shape"
            )
        candidate_id = str(row.get("candidate_id") or "")
        selected_over = str(row.get("selected_over") or "")
        reason = str(row.get(_REASON_FIELD) or "")
        if not candidate_id or not selected_over or not reason:
            raise WorkSelectionError(
                "Reviewed rerank history entries must be complete"
            )
        if candidate_id in ids:
            raise WorkSelectionError(
                f"Duplicate reviewed rerank history entry: {candidate_id}"
            )
        ids.add(candidate_id)
    return history


def _validated_policy(
    policy: Mapping[str, Any], harvest_history: Mapping[str, Any]
) -> dict[str, Any]:
    if int(policy.get("policy_version") or 0) != 11:
        raise WorkSelectionError("Unsupported work-selection policy")
    priority_classes, starting_uncovered = _validated_priority_policy(policy)
    coverage = _mapping(policy.get("coverage_family"), "coverage_family")
    validated_coverage = _validated_coverage_policy(coverage)
    prerequisite_exceptions = _validated_prerequisite_exceptions(
        coverage,
        minimum_downstream_gain=int(
            validated_coverage["minimum_prerequisite_downstream_card_gain"]
        ),
    )
    candidate_bundles, value_weights = _validated_candidate_bundles(coverage)
    harvest = _validated_harvest_history(
        harvest_history,
        minimum_gain=int(validated_coverage["minimum_complete_card_gain"]),
    )
    return {
        "policy_version": 11,
        "priority_classes": priority_classes,
        "starting_uncovered_high_risk_pairs": starting_uncovered,
        **validated_coverage,
        **harvest,
        "candidate_bundles": candidate_bundles,
        "value_weights": value_weights,
        "approved_prerequisite_exceptions": prerequisite_exceptions,
        "reviewed_rerank_history": _validated_reviewed_history(policy),
    }


def _readiness(status: str, evidence: str) -> dict[str, str]:
    return {_STATUS_FIELD: status, "evidence": evidence}


def _debt(value: int | None, basis: str) -> dict[str, Any]:
    return {"expected_count": value, "basis": basis}


def _candidate(
    *,
    candidate_id: str,
    candidate_class: str,
    universal_subsystem: str,
    compiler_readiness: Mapping[str, str],
    runtime_readiness: Mapping[str, str],
    assurance_readiness: Mapping[str, str],
    estimated_effort: str,
    reranking_reason: str,
    eligible: bool,
    reusable_piece_ids: Sequence[str] = (),
    rules_dependency_ids: Sequence[str] = (),
    affected_commander_cards: int | None = 0,
    sole_blocker_cards: int | None = 0,
    one_additional_blocker_cards: int | None = 0,
    two_additional_blocker_cards: int | None = 0,
    expected_exact_ability_gain: int | None = 0,
    expected_complete_card_gain: int | None = 0,
    expected_material_residual_reduction: int | None = 0,
    interaction_debt_introduced: Mapping[str, Any] | None = None,
    architecture_debt_removed: Mapping[str, Any] | None = None,
    direct_write_migration: Mapping[str, Any] | None = None,
    engine_extraction: Mapping[str, Any] | None = None,
    runtime_oracle_text_removal: Mapping[str, Any] | None = None,
    priority_within_class: int = 0,
    bundle: Mapping[str, Any] | None = None,
    work_state: str = "implementation",
    implementation_eligible: bool | None = None,
    measurement_task: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if candidate_class not in _CANDIDATE_CLASSES:
        raise WorkSelectionError(f"Unknown candidate class: {candidate_class}")
    bundle_payload = dict(bundle or single_candidate_bundle(candidate_id))
    if (
        set(bundle_payload) != _BUNDLE_OUTPUT_FIELDS
        or not str(bundle_payload.get("bundle_id") or "")
        or not bundle_payload.get("member_family_ids")
        or type(bundle_payload.get("synthesized")) is not bool
    ):
        raise WorkSelectionError("Work-selection bundle output is incomplete")
    effective_implementation_eligible = (
        bool(eligible)
        if implementation_eligible is None
        else bool(implementation_eligible)
    )
    measurement_payload = (
        dict(measurement_task) if measurement_task is not None else None
    )
    measurement_filter = (
        measurement_payload.get("source_corpus_filter")
        if isinstance(measurement_payload, Mapping)
        else None
    )
    if work_state == "implementation":
        if effective_implementation_eligible != bool(eligible) or measurement_payload:
            raise WorkSelectionError(
                "Implementation candidates cannot carry measurement-only state"
            )
    elif work_state == "cohort_measurement":
        if (
            not eligible
            or effective_implementation_eligible
            or not isinstance(measurement_payload, Mapping)
            or set(measurement_payload)
            != {
                "source_corpus_filter",
                "owner_operation_hypothesis",
                "grammar_boundary",
                "explicit_exclusions",
                "cards_and_residuals_to_inspect",
                "estimated_probe_hours",
                "upgrade_evidence",
                "grants_gameplay_trust",
            }
            or measurement_payload.get("grants_gameplay_trust") is not False
            or not isinstance(measurement_filter, Mapping)
            or measurement_filter.get("ability_statuses") != ["unresolved"]
            or "lowerable_untrusted_only" in measurement_filter
        ):
            raise WorkSelectionError(
                "Cohort measurements require one complete non-authoritative task"
            )
    else:
        raise WorkSelectionError(f"Unknown work state: {work_state}")
    row = {
        "candidate_id": candidate_id,
        "candidate_class": candidate_class,
        "universal_subsystem": universal_subsystem,
        "reusable_piece_ids": sorted({str(value) for value in reusable_piece_ids}),
        "rules_dependency_ids": sorted({str(value) for value in rules_dependency_ids}),
        "compiler_readiness": dict(compiler_readiness),
        "runtime_readiness": dict(runtime_readiness),
        "assurance_readiness": dict(assurance_readiness),
        "affected_commander_cards": affected_commander_cards,
        "sole_blocker_cards": sole_blocker_cards,
        "one_additional_blocker_cards": one_additional_blocker_cards,
        "two_additional_blocker_cards": two_additional_blocker_cards,
        "expected_exact_ability_gain": expected_exact_ability_gain,
        "expected_complete_card_gain": expected_complete_card_gain,
        "expected_material_residual_reduction": expected_material_residual_reduction,
        "interaction_debt_introduced": dict(interaction_debt_introduced or {}),
        "architecture_debt_removed": dict(architecture_debt_removed or {}),
        "direct_write_migration": dict(direct_write_migration or _debt(0, "none")),
        "engine_extraction": dict(engine_extraction or _debt(0, "none")),
        "runtime_oracle_text_removal": dict(
            runtime_oracle_text_removal or _debt(0, "none")
        ),
        "estimated_effort": estimated_effort,
        "reranking_reason": reranking_reason,
        "eligible": bool(eligible),
        "implementation_eligible": effective_implementation_eligible,
        "work_state": work_state,
        "measurement_task": measurement_payload,
        "priority_within_class": _nonnegative_int(
            priority_within_class, "priority_within_class"
        ),
        "bundle": bundle_payload,
    }
    if set(row) != _REQUIRED_CANDIDATE_FIELDS:
        raise WorkSelectionError("Work-selection candidate shape is incomplete")
    return row


def _runtime_oracle_candidates(
    capsules: Sequence[Mapping[str, Any]], prohibited: int
) -> list[dict[str, Any]]:
    affected_capsules = [
        row
        for row in capsules
        if int(row.get("prohibited_runtime_oracle_text_accesses") or 0) > 0
    ]
    candidates = []
    attributed_accesses = 0
    for capsule in affected_capsules:
        subsystem_id = str(capsule.get("id") or "")
        count = _nonnegative_int(
            capsule.get("prohibited_runtime_oracle_text_accesses"),
            f"{subsystem_id} prohibited runtime text count",
        )
        attributed_accesses += count
        candidates.append(
            _candidate(
                candidate_id=f"architecture:runtime-oracle-text-removal:{subsystem_id}",
                candidate_class="prohibited_runtime_semantics",
                universal_subsystem=subsystem_id,
                reusable_piece_ids=capsule.get("reusable_pieces", []),
                compiler_readiness=_readiness(
                    "partial",
                    "typed compiler inputs exist but this runtime owner still inspects prose",
                ),
                runtime_readiness=_readiness(
                    "blocked",
                    f"{count} subsystem accesses from {prohibited} prohibited total",
                ),
                assurance_readiness=_readiness(
                    "required",
                    "the bounded migration needs focused replay and interaction evidence",
                ),
                estimated_effort="small" if count <= 3 else "medium",
                reranking_reason=(
                    f"{count} prohibited runtime-text accesses remain in the existing "
                    f"{subsystem_id} typed owner and outrank card expansion."
                ),
                eligible=True,
                architecture_debt_removed={
                    "prohibited_runtime_text_accesses": count
                },
                runtime_oracle_text_removal=_debt(
                    count, "generated subsystem architecture capsule"
                ),
                priority_within_class=1_000 + count,
            )
        )
    unattributed = prohibited - attributed_accesses
    if unattributed < 0:
        raise WorkSelectionError(
            "Subsystem runtime-text counts exceed the architecture total"
        )
    if unattributed:
        candidates.append(
            _candidate(
                candidate_id="architecture:runtime-oracle-text-subsystem-attribution",
                candidate_class="prohibited_runtime_semantics",
                universal_subsystem="architecture_runtime_text_inventory",
                compiler_readiness=_readiness(
                    "not_applicable", "architecture attribution"
                ),
                runtime_readiness=_readiness(
                    "blocked",
                    f"{unattributed} of {prohibited} prohibited accesses lack a bounded "
                    "subsystem capsule",
                ),
                assurance_readiness=_readiness(
                    "required",
                    "attribute each access before selecting its behavioral migration",
                ),
                estimated_effort="medium",
                reranking_reason=(
                    "Complete subsystem attribution after the already bounded runtime-text "
                    "slices; do not treat the remainder as one implementation batch."
                ),
                eligible=True,
                architecture_debt_removed={
                    "unattributed_prohibited_runtime_text_accesses": unattributed
                },
                runtime_oracle_text_removal=_debt(
                    unattributed,
                    "generated total minus subsystem-attributed accesses",
                ),
                priority_within_class=1,
            )
        )
    return candidates


def _architecture_candidates(
    architecture_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    architecture = _mapping(
        architecture_report.get("architecture"), "architecture audit"
    )
    capsules = list(architecture.get("subsystem_capsules", []))
    missing = [
        str(row.get("id") or row.get("subsystem") or "")
        for row in architecture.get("missing_dedicated_owners", [])
    ]
    missing = sorted(value for value in missing if value)
    runtime = _mapping(
        architecture.get("runtime_oracle_text_access"), "runtime text inventory"
    )
    prohibited = _nonnegative_int(
        runtime.get("prohibited_runtime_interpretation_count"),
        "prohibited runtime interpretation count",
    )
    ownership = _mapping(
        architecture.get("direct_game_state_write_ownership"),
        "direct write ownership",
    )
    engine_writes = _nonnegative_int(
        ownership.get("writes_in_commander_engine"), "engine write count"
    )
    grandfathered = _nonnegative_int(
        ownership.get("grandfathered_engine_writes"),
        "grandfathered engine write count",
    )
    card_named = architecture.get("card_named_helpers")
    card_named_count = len(card_named) if isinstance(card_named, list) else int(bool(card_named))
    card_specific = _mapping(
        architecture.get("semantic_operation_branches"),
        "semantic operation inventory",
    ).get("card_specific_operation_branch_occurrences", {})
    card_specific_count = sum(
        int(value) for value in _mapping(card_specific, "card-specific operations").values()
    )
    runtime_candidates = _runtime_oracle_candidates(capsules, prohibited)
    return [
        _candidate(
            candidate_id="architecture:dedicated-owner-extraction",
            candidate_class="architecture_owner_or_mutation_defect",
            universal_subsystem=",".join(missing) or "all_declared_subsystems",
            compiler_readiness=_readiness("not_applicable", "ownership boundary"),
            runtime_readiness=_readiness(
                "blocked" if missing else "complete",
                f"{len(missing)} missing dedicated owners",
            ),
            assurance_readiness=_readiness(
                "required" if missing else "complete",
                "owner migrations require replay, privacy, and interaction evidence",
            ),
            estimated_effort="large" if missing else "complete",
            reranking_reason=(
                "Missing authoritative owners outrank card expansion."
                if missing
                else "All declared dedicated-owner gaps are resolved."
            ),
            eligible=bool(missing),
            architecture_debt_removed={"missing_dedicated_owners": len(missing)},
            engine_extraction=_debt(
                None if missing else 0,
                "must be measured from the selected owner capsule",
            ),
        ),
        *runtime_candidates,
        _candidate(
            candidate_id="architecture:engine-mutation-and-specificity-debt",
            candidate_class="architecture_owner_or_mutation_defect",
            universal_subsystem="commander_engine_compatibility_facade",
            compiler_readiness=_readiness("not_applicable", "architecture migration"),
            runtime_readiness=_readiness(
                (
                    "rolling_nonblocking"
                    if grandfathered or card_named_count or card_specific_count
                    else "complete"
                ),
                "typed owners exist; ordinary architecture debt is a rolling ratchet",
            ),
            assurance_readiness=_readiness(
                "required",
                "migrations require exact replay and subsystem interaction evidence",
            ),
            estimated_effort="large",
            reranking_reason=(
                f"{grandfathered} grandfathered engine writes, {card_named_count} card-named "
                f"helpers, and {card_specific_count} card-specific operation branches remain, "
                "but no current correctness defect makes this foreground work."
            ),
            eligible=False,
            architecture_debt_removed={
                "grandfathered_engine_writes": grandfathered,
                "card_named_helpers": card_named_count,
                "card_specific_operation_branches": card_specific_count,
            },
            direct_write_migration=_debt(
                grandfathered, "generated direct-write ownership inventory"
            ),
            engine_extraction=_debt(
                engine_writes, "current CommanderEngine direct-write inventory"
            ),
        ),
    ]


def _system_candidates(
    compact: Mapping[str, Any],
    readiness: Mapping[str, Any],
    reusable_delta: Mapping[str, Any],
    *,
    assurance_baseline: int,
) -> list[dict[str, Any]]:
    compact_closed = compact.get("closed") is True
    validation = _mapping(readiness.get("validation"), "platform validation")
    replay = str(validation.get("replay") or "")
    privacy = str(validation.get("privacy") or "")
    replay_privacy_closed = replay.startswith("pass") and privacy.startswith("pass")
    interaction = _mapping(
        reusable_delta.get("interaction_coverage"), "interaction coverage"
    )
    applicable = _nonnegative_int(
        interaction.get("applicable_high_risk_pairs"), "applicable high-risk pairs"
    )
    covered = _nonnegative_int(
        interaction.get("covered_high_risk_pairs"), "covered high-risk pairs"
    )
    if covered > applicable:
        raise WorkSelectionError("Covered high-risk pairs exceed applicable pairs")
    uncovered = applicable - covered
    assurance_gate_open = uncovered > assurance_baseline
    return [
        _candidate(
            candidate_id="ci:compact-card-dependency-closure",
            candidate_class="ci_correctness",
            universal_subsystem="deterministic_ci_card_data",
            compiler_readiness=_readiness("not_applicable", "CI dependency closure"),
            runtime_readiness=_readiness(
                "complete" if compact_closed else "blocked",
                f"closed={compact_closed}; {compact.get('card_count', 0)} cards and "
                f"{compact.get('requirements_discovered', 0)} requirements",
            ),
            assurance_readiness=_readiness(
                "complete" if compact_closed else "blocked",
                "canonical compact dependency validator",
            ),
            estimated_effort="complete" if compact_closed else "medium",
            reranking_reason=(
                "Compact CI dependency coverage is closed."
                if compact_closed
                else "A deterministic CI omission outranks feature work."
            ),
            eligible=not compact_closed,
        ),
        _candidate(
            candidate_id="correctness:replay-privacy-recovery",
            candidate_class="replay_privacy_defect",
            universal_subsystem="replay_and_projection",
            compiler_readiness=_readiness("not_applicable", "runtime correctness"),
            runtime_readiness=_readiness(
                "complete" if replay_privacy_closed else "blocked",
                f"replay={replay}; privacy={privacy}",
            ),
            assurance_readiness=_readiness(
                "complete" if replay_privacy_closed else "blocked",
                "generated platform validation",
            ),
            estimated_effort="complete" if replay_privacy_closed else "unknown",
            reranking_reason=(
                "No current generated replay or privacy defect is recorded."
                if replay_privacy_closed
                else "Replay or hidden-information correctness outranks feature work."
            ),
            eligible=not replay_privacy_closed,
        ),
        _candidate(
            candidate_id="assurance:critical-interaction-recovery",
            candidate_class="interaction_assurance",
            universal_subsystem="cross_owner_interactions",
            compiler_readiness=_readiness("not_applicable", "behavioral assurance"),
            runtime_readiness=_readiness("implemented", "existing typed owners"),
            assurance_readiness=_readiness(
                "blocked" if assurance_gate_open else "exit_gate_satisfied",
                f"{covered}/{applicable} covered; {uncovered} uncovered; "
                f"starting baseline {assurance_baseline}",
            ),
            estimated_effort="medium" if assurance_gate_open else "ongoing",
            reranking_reason=(
                "Uncovered high-risk interactions remain above the verified "
                "stabilization baseline."
                if assurance_gate_open
                else (
                    "The stabilization exit gate is satisfied and no uncovered "
                    "high-risk interaction debt remains."
                    if uncovered == 0
                    else "The stabilization exit gate is satisfied at or below "
                    "the configured baseline, though uncovered high-risk "
                    "interaction debt remains."
                )
            ),
            eligible=assurance_gate_open,
            interaction_debt_introduced={
                "applicable_high_risk_pairs": applicable,
                "covered_high_risk_pairs": covered,
                "uncovered_high_risk_pairs": uncovered,
                "starting_uncovered_high_risk_pairs": assurance_baseline,
            },
        ),
    ]


def _rules_candidate(selected_batch: Mapping[str, Any]) -> dict[str, Any]:
    rules = list(selected_batch.get("rules", []))
    return _candidate(
        candidate_id=f"rules:{selected_batch.get('batch_id') or 'selected-batch'}",
        candidate_class="rules_foundation",
        universal_subsystem=str(selected_batch.get("subsystem_id") or "rules"),
        reusable_piece_ids=selected_batch.get("target_capability_ids", []),
        rules_dependency_ids=selected_batch.get("rule_ids", []),
        compiler_readiness=_readiness(
            "partial", "selected dependency-ready bounded rules batch"
        ),
        runtime_readiness=_readiness(
            "partial",
            f"{len(rules)} selected blocked behavioral rule records",
        ),
        assurance_readiness=_readiness(
            "measurement_required",
            f"{len(selected_batch.get('executable_test_ids', []))} existing test identities",
        ),
        affected_commander_cards=None,
        sole_blocker_cards=None,
        one_additional_blocker_cards=None,
        two_additional_blocker_cards=None,
        expected_exact_ability_gain=None,
        expected_complete_card_gain=None,
        expected_material_residual_reduction=None,
        interaction_debt_introduced={
            _STATUS_FIELD: "must_be_measured_before_implementation"
        },
        estimated_effort="medium",
        reranking_reason=(
            "The rules queue remains dependency-ready, but its complete-card gain is "
            "unknown. Measure a broad harvest or a concrete correctness defect before "
            "promoting it over the generated foreground."
        ),
        eligible=False,
    )


def _fail_closed_foundation_candidates(
    interactions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for index, raw in enumerate(interactions.get("pairs", [])):
        pair = _mapping(raw, f"reusable-piece interaction pair {index}")
        assurance_kinds = {
            str(value) for value in pair.get("evidence_assurance_kinds", [])
        }
        if (
            pair.get("high_risk") is not True
            or pair.get("covered") is not True
            or "fail_closed_runtime_admission" not in assurance_kinds
        ):
            continue
        residuals = [
            str(value)
            for value in pair.get("piece_ids", [])
            if str(value).startswith("residual.")
        ]
        if not residuals or len(residuals) > 2:
            raise WorkSelectionError(
                "High-risk fail-closed interaction evidence must contain one or two "
                "residual families"
            )
        for residual_id in residuals:
            grouped.setdefault(residual_id, []).append(pair)

    candidates = []
    for residual_id, pairs in sorted(grouped.items()):
        neighbors = sorted(
            {
                str(piece_id)
                for pair in pairs
                for piece_id in pair.get("piece_ids", [])
                if str(piece_id) != residual_id
            }
        )
        affected_cards = max(int(pair.get("card_count") or 0) for pair in pairs)
        pair_count = len(pairs)
        residual_parts = residual_id.split(".")
        subsystem = residual_parts[1] if len(residual_parts) > 2 else "rules"
        candidates.append(
            _candidate(
                candidate_id=f"interaction-implementation:{residual_id}",
                candidate_class="rules_foundation",
                universal_subsystem=subsystem,
                reusable_piece_ids=[residual_id, *neighbors],
                compiler_readiness=_readiness(
                    "missing_typed_owner",
                    f"{residual_id} remains a material compiler residual",
                ),
                runtime_readiness=_readiness(
                    "safe_but_unimplemented",
                    f"{pair_count} high-risk pairs are rejected at runtime admission",
                ),
                assurance_readiness=_readiness(
                    "fail_closed_only",
                    "replace rejection-only evidence with behavioral composition "
                    "when the shared owner is implemented",
                ),
                affected_commander_cards=affected_cards,
                sole_blocker_cards=None,
                one_additional_blocker_cards=None,
                two_additional_blocker_cards=None,
                expected_exact_ability_gain=None,
                expected_complete_card_gain=None,
                expected_material_residual_reduction=None,
                interaction_debt_introduced={
                    _STATUS_FIELD: "safe_but_unimplemented",
                    "high_risk_fail_closed_pair_incidence": pair_count,
                    "neighbor_count": len(neighbors),
                },
                estimated_effort="large" if pair_count >= 20 else "medium",
                reranking_reason=(
                    f"{pair_count} applicable high-risk pairs touching up to "
                    f"{affected_cards} corpus cards are currently safe only because at "
                    f"least one side, including {residual_id}, is rejected. Preserve this "
                    "as implementation pressure, but covered fail-closed evidence alone "
                    "does not block a measured broad harvest."
                ),
                eligible=False,
                priority_within_class=(
                    pair_count * 1_000_000
                    + affected_cards * 1_000
                    + len(neighbors)
                ),
            )
        )
    return candidates


def _frontier_candidate_class(family_id: str) -> str:
    if family_id.startswith(("effect_clause:", "activated_effect:")):
        return "compiler_harvest"
    if family_id.startswith("keyword_dependency:"):
        return "card_family"
    return "rules_foundation"


def _frontier_decision(
    *,
    candidate_id: str,
    complete_gain: int,
    ability_gain: int,
    residual_gain: int,
    lowerable_untrusted_abilities: int,
    sole_blockers: int,
    prerequisites: Sequence[str],
    effort: str,
    policy: Mapping[str, Any],
) -> tuple[str, bool, str]:
    excluded = effort in policy["excluded_efforts"]
    broad = complete_gain >= int(policy["minimum_complete_card_gain"])
    major_ability_harvest = bool(
        ability_gain >= int(policy["minimum_exact_ability_gain"])
        and lowerable_untrusted_abilities
        >= int(policy["minimum_exact_ability_gain"])
    )
    major_residual_harvest = bool(
        residual_gain >= int(policy["minimum_material_residual_reduction"])
        and lowerable_untrusted_abilities
        >= int(policy["minimum_material_residual_reduction"])
    )
    structural = complete_gain == 0 and sole_blockers == 0
    exceptions = {
        str(row["candidate_id"])
        for row in policy["approved_prerequisite_exceptions"]
    }
    exception_allowed = bool(
        candidate_id in exceptions
        and complete_gain >= int(policy["minimum_prerequisite_complete_card_gain"])
        and int(policy["consecutive_subthreshold_harvests"])
        < int(policy["maximum_consecutive_prerequisite_exceptions"])
    )
    if prerequisites:
        return (
            "blocked_by_prerequisites",
            False,
            "Blocked prerequisites keep this high-yield frontier behind ready work.",
        )
    if excluded:
        return (
            "excluded_effort",
            False,
            f"Estimated effort {effort} is excluded from a bounded foreground.",
        )
    if structural:
        return (
            "structural_nonexecuting",
            False,
            "This aggregate has no executable complete-card gain or sole blockers; "
            "classify its child grammars instead of selecting the structural carrier.",
        )
    if broad:
        return (
            "candidate",
            True,
            "Meets the normal measured complete-card harvest floor and remains "
            "behind higher-priority correctness gates.",
        )
    if major_ability_harvest:
        return (
            "major_exact_ability_harvest",
            True,
            "Meets the measured major exact-ability floor inside one reusable "
            "grammar boundary.",
        )
    if major_residual_harvest:
        return (
            "major_material_residual_harvest",
            True,
            "Meets the measured material-residual reduction floor inside one "
            "coherent reusable boundary.",
        )
    if exception_allowed:
        return (
            "approved_prerequisite_exception",
            True,
            "A reviewed prerequisite exception supplies measured downstream card "
            "gain and the consecutive-exception budget remains open.",
        )
    return (
        "requires_broader_bundle",
        False,
        "This family does not meet the card, exact-ability, or material-residual "
        "harvest floor; bundle it with coherent sibling grammar.",
    )


def _frontier_candidate(
    row: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    family_id = str(row.get("family_id") or "")
    candidate_id = f"frontier:{family_id}"
    prerequisites = [str(value) for value in row.get("prerequisites", [])]
    complete_gain = int(row.get("expected_exact_card_gain") or 0)
    ability_gain = int(row.get("expected_exact_ability_gain") or 0)
    residual_gain = int(row.get("expected_material_residual_gain") or 0)
    sole_blockers = int(row.get("sole_blocker_cards") or 0)
    effort = str(row.get("estimated_effort") or "unknown")
    readiness, eligible, reason = _frontier_decision(
        candidate_id=candidate_id,
        complete_gain=complete_gain,
        ability_gain=ability_gain,
        residual_gain=residual_gain,
        lowerable_untrusted_abilities=int(
            row.get("lowerable_untrusted_abilities") or 0
        ),
        sole_blockers=sole_blockers,
        prerequisites=prerequisites,
        effort=effort,
        policy=policy,
    )
    one_additional = int(row.get("one_additional_blocker_cards") or 0)
    two_additional = int(row.get("two_additional_blocker_cards") or 0)
    return _candidate(
        candidate_id=candidate_id,
        candidate_class=_frontier_candidate_class(family_id),
        universal_subsystem=str(row.get("base_family") or family_id),
        rules_dependency_ids=prerequisites,
        compiler_readiness=_readiness(
            str(row.get("runtime_compiler_readiness") or "unknown"),
            "generated card-unlock frontier",
        ),
        runtime_readiness=_readiness(
            readiness, f"{len(prerequisites)} recorded prerequisites"
        ),
        assurance_readiness=_readiness(
            "required_before_trust",
            f"interaction risk={row.get('interaction_risk') or 'unknown'}",
        ),
        affected_commander_cards=int(row.get("affected_cards") or 0),
        sole_blocker_cards=sole_blockers,
        one_additional_blocker_cards=one_additional,
        two_additional_blocker_cards=two_additional,
        expected_exact_ability_gain=ability_gain,
        expected_complete_card_gain=complete_gain,
        expected_material_residual_reduction=residual_gain,
        interaction_debt_introduced={
            _STATUS_FIELD: "unmeasured",
            "risk": str(row.get("interaction_risk") or "unknown"),
        },
        estimated_effort=effort,
        reranking_reason=reason,
        eligible=eligible,
        bundle=atomic_frontier_bundle(
            candidate_id=candidate_id,
            family_id=family_id,
            base_family=str(row.get("base_family") or family_id),
            effort=effort,
            complete_cards=complete_gain,
            exact_abilities=ability_gain,
            residuals=residual_gain,
            one_additional=one_additional,
            two_additional=two_additional,
            weights=policy["value_weights"],
        ),
    )


def _synthesized_frontier_candidates(
    frontier: Mapping[str, Any],
    policy: Mapping[str, Any],
    cohort_measurement_artifact: Mapping[str, Any],
    *,
    completed_bundle_ids: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        measurements = validated_candidate_frontier_measurements(
            frontier,
            policy["candidate_bundles"],
            policy["value_weights"],
            cohort_measurement_artifact,
            policy,
            completed_bundle_ids=completed_bundle_ids,
        )
    except WorkSelectionBundleError as exc:
        raise WorkSelectionError(str(exc)) from exc
    for measurement in measurements:
        bundle_policy = measurement["policy"]
        bundle_id = str(bundle_policy["bundle_id"])
        member_ids = measurement["member_ids"]
        gains = measurement["gains"]
        prerequisites = measurement["prerequisites"]
        implementation_hours = measurement["implementation_hours"]
        effort = estimated_bundle_effort(implementation_hours)
        readiness, eligible, reason = _frontier_decision(
            candidate_id=bundle_id,
            complete_gain=gains["exact_cards"],
            ability_gain=gains["exact_abilities"],
            residual_gain=gains["material_residuals"],
            lowerable_untrusted_abilities=sum(
                int(row.get("lowerable_untrusted_abilities") or 0)
                for row in measurement["members"]
            ),
            sole_blockers=gains["exact_cards"],
            prerequisites=prerequisites,
            effort=effort,
            policy=policy,
        )
        effective_measurement_status, demotion_reason = bundle_measurement_decision(
            str(bundle_policy["measurement_status"]),
            bool(measurement["bounded_executable_verified"]),
            measurement["measurement_outcome"],
        )
        if effective_measurement_status == "measured_nonviable":
            readiness, eligible, reason = "measured_below_harvest_floor", False, str(demotion_reason)
        elif demotion_reason is not None:
            readiness = "requires_bounded_cohort"
            eligible = False
            reason = demotion_reason
        contexts = [str(value) for value in bundle_policy["source_contexts"]]
        interaction_risks = measurement["interaction_risks"]
        result.append(
            _candidate(
                candidate_id=bundle_id,
                candidate_class="compiler_harvest",
                universal_subsystem="compiler_bundle_hypothesis",
                reusable_piece_ids=[
                    "residual." + value.replace(":", ".", 1)
                    for value in member_ids
                ],
                rules_dependency_ids=prerequisites,
                compiler_readiness=_readiness(
                    "bundle_hypothesis",
                    "Static shared-owner grammar validated against current frontier members",
                ),
                runtime_readiness=_readiness(
                    readiness, f"{len(prerequisites)} recorded prerequisites"
                ),
                assurance_readiness=_readiness(
                    "required_before_trust",
                    "interaction risks=" + ",".join(interaction_risks),
                ),
                affected_commander_cards=gains["affected_cards"],
                sole_blocker_cards=gains["exact_cards"],
                one_additional_blocker_cards=gains[
                    "one_additional_blocker_cards"
                ],
                two_additional_blocker_cards=gains[
                    "two_additional_blocker_cards"
                ],
                expected_exact_ability_gain=gains["exact_abilities"],
                expected_complete_card_gain=gains["exact_cards"],
                expected_material_residual_reduction=gains[
                    "material_residuals"
                ],
                interaction_debt_introduced={
                    _STATUS_FIELD: "unmeasured",
                    "risks": interaction_risks,
                },
                estimated_effort=effort,
                reranking_reason=(
                    f"{reason} The bundle shares {len(bundle_policy['canonical_owner_ids'])} "
                    f"canonical owners across {len(contexts)} source contexts and "
                    f"is predicted at {measurement['cards_per_hour']} complete cards "
                    "per cycle hour."
                ),
                eligible=eligible,
                bundle={
                    "bundle_id": bundle_id,
                    "member_family_ids": member_ids,
                    "canonical_owner_ids": list(
                        bundle_policy["canonical_owner_ids"]
                    ),
                    "source_contexts": contexts,
                    "normalized_literal_parameters": list(
                        bundle_policy["normalized_literal_parameters"]
                    ),
                    "shared_dependencies": list(
                        bundle_policy["shared_dependencies"]
                    ),
                    "shared_grammar": str(bundle_policy["shared_grammar"]),
                    "estimated_implementation_hours": implementation_hours,
                    "estimated_probe_hours": int(
                        bundle_policy["estimated_probe_hours"]
                    ),
                    "estimated_generation_hours": measurement["generation_hours"],
                    "estimated_cycle_hours": measurement["cycle_hours"],
                    "predicted_complete_cards_per_cycle_hour": measurement[
                        "cards_per_hour"
                    ],
                    "predicted_normalized_value_per_cycle_hour": measurement[
                        "value_per_hour"
                    ],
                    "expected_downstream_closure": {
                        "description": str(
                            bundle_policy["expected_downstream_closure"]
                        ),
                        "one_additional_blocker_cards": gains[
                            "one_additional_blocker_cards"
                        ],
                        "two_additional_blocker_cards": gains[
                            "two_additional_blocker_cards"
                        ],
                    },
                    "explicit_exclusions": list(
                        bundle_policy["explicit_exclusions"]
                    ),
                    "measurement_status": effective_measurement_status,
                    "synthesized": True,
                },
            )
        )
    return result


def _frontier_candidates(
    frontier: Mapping[str, Any], policy: Mapping[str, Any]
) -> list[dict[str, Any]]:
    retained_ids = {
        str(row["selected_over"])
        for row in policy["reviewed_rerank_history"]
        if str(row["selected_over"]).startswith("frontier:")
    }
    retained_ids.update(
        str(row["candidate_id"])
        for row in policy["approved_prerequisite_exceptions"]
    )
    candidates = []
    for row in frontier.get("family_candidates", []):
        candidate_id = f"frontier:{row.get('family_id') or ''}"
        gains = (
            int(row.get("expected_exact_card_gain") or 0),
            int(row.get("expected_exact_ability_gain") or 0),
            int(row.get("expected_material_residual_gain") or 0),
        )
        thresholds = (
            int(policy["minimum_complete_card_gain"]),
            int(policy["minimum_exact_ability_gain"]),
            int(policy["minimum_material_residual_reduction"]),
        )
        serious = any(gain >= threshold for gain, threshold in zip(gains, thresholds))
        effort = str(row.get("estimated_effort") or "unknown")
        if candidate_id not in retained_ids and (
            not serious or effort in policy["excluded_efforts"]
        ):
            continue
        candidates.append(_frontier_candidate(row, policy))
    candidates.sort(
        key=lambda row: (
            not bool(row["eligible"]),
            *(-int(row[field] or 0) for field in policy["coverage_rank_order"]),
            str(row["candidate_id"]),
        )
    )
    limited = candidates[: int(policy["candidate_limit"])]
    limited_ids = {str(row["candidate_id"]) for row in limited}
    limited.extend(
        row
        for row in candidates
        if str(row["candidate_id"]) in retained_ids
        and str(row["candidate_id"]) not in limited_ids
    )
    return limited


def _harvest_outcome_candidate(validated: Mapping[str, Any]) -> dict[str, Any]:
    pending = validated["pending_transition"]
    is_pending = validated["semantic_outcome_status"] == "pending"
    transition_id = (
        str(pending.get("transition_id") or "")
        if isinstance(pending, Mapping)
        else ""
    )
    return _candidate(
        candidate_id="ci:materialize-harvest-outcome",
        candidate_class="ci_correctness",
        universal_subsystem="generated_harvest_provenance",
        compiler_readiness=_readiness(
            "pending_outcome" if is_pending else "current",
            transition_id or "latest semantic support has an immutable outcome",
        ),
        runtime_readiness=_readiness(
            "blocked" if is_pending else "complete",
            "downstream evidence only; no gameplay authority",
        ),
        assurance_readiness=_readiness(
            "not_applicable",
            "materializing immutable corpus receipts changes no runtime behavior",
        ),
        estimated_effort="small" if is_pending else "complete",
        reranking_reason=(
            "Complete the declared semantic transition's content receipts so "
            "the current feature fixed point can materialize its outcome before "
            "another implementation cohort is selected."
            if is_pending
            else "Every current semantic support transition has a downstream outcome."
        ),
        eligible=is_pending,
    )


def _cohort_measurement_candidate(
    candidates: Sequence[Mapping[str, Any]],
    frontier: Mapping[str, Any],
) -> dict[str, Any] | None:
    spec = cohort_measurement_spec(candidates, frontier)
    return _candidate(**spec) if spec is not None else None

def _work_selection_candidates(
    *,
    selected_batch: Mapping[str, Any],
    validated: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> list[dict[str, Any]]:
    required_inputs = {
        "architecture_audit",
        "card_unlock_frontier",
        "cohort_measurements",
        "harvest_outcome_history",
        "compact_ci_dependencies",
        "platform_readiness",
        "reusable_piece_delta",
        "reusable_piece_interactions",
    }
    if set(inputs) != required_inputs:
        raise WorkSelectionError(
            "Work-selection inputs must be the canonical generated reports"
        )
    frontier = _mapping(inputs["card_unlock_frontier"], "card-unlock frontier")
    completed_bundle_ids = {str(row["bundle_id"]) for row in validated["harvest_outcome_history"]}
    candidates = [
        _harvest_outcome_candidate(validated),
        *_system_candidates(
            _mapping(inputs["compact_ci_dependencies"], "compact CI report"),
            _mapping(inputs["platform_readiness"], "platform readiness"),
            _mapping(inputs["reusable_piece_delta"], "reusable-piece delta"),
            assurance_baseline=int(validated["starting_uncovered_high_risk_pairs"]),
        ),
        *_architecture_candidates(
            _mapping(inputs["architecture_audit"], "architecture audit")
        ),
        *_fail_closed_foundation_candidates(
            _mapping(
                inputs["reusable_piece_interactions"],
                "reusable-piece interactions",
            )
        ),
        _rules_candidate(selected_batch),
        *_frontier_candidates(
            frontier,
            validated,
        ),
        *_synthesized_frontier_candidates(
            frontier,
            validated,
            _mapping(
                inputs["cohort_measurements"],
                "work-selection cohort measurements",
            ),
            completed_bundle_ids=completed_bundle_ids,
        ),
    ]
    candidates = [row for row in candidates if row["candidate_id"] not in completed_bundle_ids]
    measurement = _cohort_measurement_candidate(candidates, frontier)
    if measurement is not None:
        candidates.append(measurement)
    return candidates


def _validate_candidate_context(
    candidates: Sequence[Mapping[str, Any]], validated: Mapping[str, Any]
) -> None:
    ids = [str(row["candidate_id"]) for row in candidates]
    if len(ids) != len(set(ids)):
        raise WorkSelectionError("Work-selection candidate ids must be unique")
    candidate_ids = set(ids)
    for row in validated["approved_prerequisite_exceptions"]:
        candidate_id = str(row["candidate_id"])
        if candidate_id not in candidate_ids:
            raise WorkSelectionError(
                "Approved prerequisite exception must reference a current serious "
                f"frontier candidate: {candidate_id}"
            )
    for row in validated["reviewed_rerank_history"]:
        selected_over = str(row["selected_over"])
        if selected_over not in candidate_ids:
            raise WorkSelectionError(
                "Reviewed rerank history selected_over must reference a "
                f"current candidate: {selected_over}"
            )
        if str(row["candidate_id"]) == selected_over:
            raise WorkSelectionError(
                "Reviewed rerank history cannot select a candidate over itself"
            )


def _rank_candidates(
    candidates: list[dict[str, Any]], validated: Mapping[str, Any]
) -> dict[str, Any] | None:
    priorities = {
        candidate_class: index
        for index, candidate_class in enumerate(validated["priority_classes"])
    }
    candidates.sort(
        key=lambda row: (
            not bool(row["eligible"]),
            bool(row["eligible"] and not row["implementation_eligible"]),
            priorities[str(row["candidate_class"])],
            -float(
                row["bundle"].get(
                    "predicted_complete_cards_per_cycle_hour"
                )
                or 0
            ),
            -len(row["bundle"].get("source_contexts") or ()),
            -int(row["one_additional_blocker_cards"] or 0),
            -int(row["two_additional_blocker_cards"] or 0),
            -float(
                row["bundle"].get(
                    "predicted_normalized_value_per_cycle_hour"
                )
                or 0
            ),
            -int(row["priority_within_class"]),
            *(
                -int(row[field] or 0)
                for field in validated["coverage_rank_order"]
            ),
            str(row["candidate_id"]),
        )
    )
    selected = next(
        (row for row in candidates if row["implementation_eligible"]), None
    )
    if selected is None:
        selected = next((row for row in candidates if row["eligible"]), None)
    for index, row in enumerate(candidates, start=1):
        row["rank"] = index
        if row is selected:
            selection_state = "selected"
        elif row["eligible"]:
            selection_state = "deferred"
        elif row["runtime_readiness"].get(_STATUS_FIELD) == "complete" or row[
            "assurance_readiness"
        ].get(_STATUS_FIELD) in {"complete", "exit_gate_satisfied"}:
            selection_state = "complete"
        else:
            selection_state = "blocked"
        row["selection_state"] = selection_state
    return selected


def selected_work_candidate(
    work_selection: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Validate and return the selected candidate, including explicit none."""

    if not isinstance(work_selection, Mapping):
        raise WorkSelectionError("Work selection must be an object")
    candidates = work_selection.get("candidates")
    if not isinstance(candidates, list) or any(
        not isinstance(candidate, Mapping) for candidate in candidates
    ):
        raise WorkSelectionError("Work-selection candidates must be objects")
    eligible = [candidate for candidate in candidates if candidate.get("eligible") is True]
    declared_count = work_selection.get("eligible_candidate_count")
    if type(declared_count) is not int or declared_count != len(eligible):
        raise WorkSelectionError(
            "Work-selection eligible candidate count does not match candidates"
        )
    implementation_eligible = [
        candidate
        for candidate in candidates
        if candidate.get("implementation_eligible") is True
    ]
    declared_implementation_count = work_selection.get(
        "implementation_eligible_candidate_count"
    )
    if (
        type(declared_implementation_count) is not int
        or declared_implementation_count != len(implementation_eligible)
    ):
        raise WorkSelectionError(
            "Work-selection implementation-eligible count does not match candidates"
        )
    selected_id = work_selection.get("selected_candidate_id")
    selected_rows = [
        candidate
        for candidate in candidates
        if candidate.get("selection_state") == "selected"
    ]
    if selected_id is None:
        if eligible or selected_rows:
            raise WorkSelectionError(
                "No selected candidate is valid only when none are eligible"
            )
        return None
    if type(selected_id) is not str or not selected_id:
        raise WorkSelectionError(
            "Selected work candidate ID must be nonempty or null"
        )
    matches = [
        candidate
        for candidate in candidates
        if candidate.get("candidate_id") == selected_id
    ]
    if (
        len(matches) != 1
        or matches[0].get("eligible") is not True
        or matches[0].get("selection_state") != "selected"
        or selected_rows != matches
    ):
        raise WorkSelectionError(
            "Selected work candidate must name one eligible selected row"
        )
    if (
        implementation_eligible
        and matches[0].get("implementation_eligible") is not True
    ):
        raise WorkSelectionError(
            "A cohort measurement cannot outrank implementation-eligible work"
        )
    return matches[0]


def _selection_policy_payload(validated: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "starting_uncovered_high_risk_pairs": validated[
            "starting_uncovered_high_risk_pairs"
        ],
        "minimum_complete_card_gain": validated["minimum_complete_card_gain"],
        "minimum_exact_ability_gain": validated["minimum_exact_ability_gain"],
        "minimum_material_residual_reduction": validated[
            "minimum_material_residual_reduction"
        ],
        "minimum_prerequisite_complete_card_gain": validated[
            "minimum_prerequisite_complete_card_gain"
        ],
        "minimum_prerequisite_downstream_card_gain": validated[
            "minimum_prerequisite_downstream_card_gain"
        ],
        "maximum_consecutive_prerequisite_exceptions": validated[
            "maximum_consecutive_prerequisite_exceptions"
        ],
        "consecutive_subthreshold_harvests": validated[
            "consecutive_subthreshold_harvests"
        ],
        "observed_harvest_count": len(validated["harvest_outcome_history"]),
        "observed_subthreshold_harvest_count": validated[
            "subthreshold_harvests"
        ],
        "observed_card_gain_absolute_error": validated[
            "card_gain_absolute_error"
        ],
        "observed_expected_gain_count": validated["known_expected_harvests"],
        "harvest_outcome_status": validated["semantic_outcome_status"],
        "pending_harvest_transition_id": (
            str(validated["pending_transition"].get("transition_id") or "")
            if validated["pending_transition"] is not None
            else None
        ),
        "coverage_rank_order": validated["coverage_rank_order"],
        "coverage_candidate_limit": validated["candidate_limit"],
        "excluded_efforts": sorted(validated["excluded_efforts"]),
        "approved_prerequisite_exceptions": validated[
            "approved_prerequisite_exceptions"
        ],
        "candidate_bundle_ids": [
            str(row["bundle_id"]) for row in validated["candidate_bundles"]
        ],
        "value_weights": validated["value_weights"],
    }


def build_work_selection(
    *,
    selected_batch: Mapping[str, Any],
    policy: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    harvest_history = _mapping(
        inputs.get("harvest_outcome_history"), "harvest_outcome_history"
    )
    validated = _validated_policy(policy, harvest_history)
    candidates = _work_selection_candidates(
        selected_batch=selected_batch,
        validated=validated,
        inputs=inputs,
    )
    _validate_candidate_context(candidates, validated)
    selected = _rank_candidates(candidates, validated)
    payload = {
        "schema_version": WORK_SELECTION_SCHEMA_VERSION,
        "policy_version": validated["policy_version"],
        "priority_classes": validated["priority_classes"],
        "source_fingerprints": work_selection_source_fingerprints(inputs),
        "selection_policy": _selection_policy_payload(validated),
        "harvest_outcome_history": validated["harvest_outcome_history"],
        "reviewed_rerank_history": validated["reviewed_rerank_history"],
        "selected_candidate_id": (
            str(selected["candidate_id"]) if selected is not None else None
        ),
        "serious_candidate_count": len(candidates),
        "eligible_candidate_count": sum(bool(row["eligible"]) for row in candidates),
        "implementation_eligible_candidate_count": sum(
            bool(row["implementation_eligible"]) for row in candidates
        ),
        "candidates": candidates,
    }
    payload["fingerprint"] = _hash(payload)
    return payload


__all__ = [
    "WORK_SELECTION_SCHEMA_VERSION",
    "WorkSelectionError",
    "build_work_selection",
    "load_work_selection_inputs",
    "selected_work_candidate",
]
