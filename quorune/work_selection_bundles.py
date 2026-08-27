from __future__ import annotations

import hashlib
import re
from typing import Any, Collection, Mapping, Sequence

from quorune.util import stable_json
from quorune.work_selection_evidence import (
    validate_work_selection_cohort_measurements,
    WorkSelectionCohortMeasurementError,
)


_BUNDLE_CONTEXTS = {"activated", "modal", "setup", "spell", "triggered"}
_BUNDLE_MEASUREMENT_STATUSES = {
    "bounded_executable",
    "generated_probe",
    "upper_bound_only",
}
_BUNDLE_OWNER = re.compile(r"^(?:capability|component):[A-Za-z0-9_.:-]+$")
_MEASUREMENT_METHOD = "frontier-sole-blocker-closure-v1"
_MEASUREMENT_FINGERPRINT_SCHEMA = 1
_EFFORT_HOURS = {
    "small": 5,
    "medium": 10,
    "large": 20,
    "very_large": 60,
    "unknown": 30,
}
_SOURCE_CONTEXTS_BY_BASE = {
    "activated_effect": ["activated"],
    "effect_clause": ["modal", "spell", "triggered"],
    "keyword_dependency": ["activated", "spell", "triggered"],
}


class WorkSelectionBundleError(ValueError):
    pass


def single_candidate_bundle(candidate_id: str) -> dict[str, Any]:
    return {
        "bundle_id": candidate_id,
        "member_family_ids": [candidate_id],
        "canonical_owner_ids": [],
        "source_contexts": [],
        "normalized_literal_parameters": [],
        "shared_dependencies": [],
        "shared_grammar": None,
        "estimated_implementation_hours": None,
        "estimated_probe_hours": None,
        "estimated_generation_hours": None,
        "estimated_cycle_hours": None,
        "predicted_complete_cards_per_cycle_hour": None,
        "predicted_normalized_value_per_cycle_hour": None,
        "expected_downstream_closure": None,
        "explicit_exclusions": [],
        "measurement_status": "not_applicable",
        "synthesized": False,
    }


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkSelectionBundleError(f"{label} must be an object")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise WorkSelectionBundleError(
            f"{label} must be a nonnegative integer"
        )
    return value


def _validate_measurement_probe(
    row: Mapping[str, Any],
    *,
    measurement_status: str,
) -> None:
    probe_id = row.get("measurement_probe_id")
    if measurement_status == "generated_probe":
        if not isinstance(probe_id, str) or not probe_id:
            raise WorkSelectionBundleError(
                "Generated cohort measurements require a probe ID"
            )
    elif probe_id is not None:
        raise WorkSelectionBundleError(
            "Only generated cohort measurements may carry a probe ID"
        )


def validate_bundle_policy(
    coverage: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
    weight_fields = {
        "complete_card": 1,
        "exact_ability": 1,
        "material_residual": 1,
        "one_additional_blocker_card": 0,
        "two_additional_blocker_card": 0,
    }
    raw_weights = _mapping(coverage.get("value_weights"), "value_weights")
    if set(raw_weights) != set(weight_fields):
        raise WorkSelectionBundleError(
            "Bundle value weights have an invalid shape"
        )
    weights = {
        field: _nonnegative_int(raw_weights.get(field), field)
        for field in weight_fields
    }
    if any(weights[field] < minimum for field, minimum in weight_fields.items()):
        raise WorkSelectionBundleError("Bundle value weights are incomplete")
    expected = {
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
        "expected_downstream_closure",
        "explicit_exclusions",
        "measurement_status",
        "measurement_probe_id",
    }
    bundles = list(coverage.get("candidate_bundles", []))
    seen: set[str] = set()
    for index, raw in enumerate(bundles):
        row = _mapping(raw, f"candidate_bundles[{index}]")
        if set(row) != expected:
            raise WorkSelectionBundleError(
                "Candidate bundle has an invalid shape"
            )
        bundle_id = str(row.get("bundle_id") or "")
        members = [str(value) for value in row.get("member_family_ids", [])]
        owners = [str(value) for value in row.get("canonical_owner_ids", [])]
        contexts = [str(value) for value in row.get("source_contexts", [])]
        parameters = [
            str(value) for value in row.get("normalized_literal_parameters", [])
        ]
        dependencies = [str(value) for value in row.get("shared_dependencies", [])]
        exclusions = [str(value) for value in row.get("explicit_exclusions", [])]
        measurement_status = str(row.get("measurement_status") or "")
        if (
            not bundle_id.startswith("bundle:")
            or bundle_id in seen
            or len(members) < 2
            or members != sorted(set(members))
            or any(":" not in value for value in members)
            or not owners
            or owners != sorted(set(owners))
            or any(not _BUNDLE_OWNER.fullmatch(value) for value in owners)
            or not contexts
            or (
                len(contexts) < 2
                and measurement_status != "bounded_executable"
            )
            or contexts != sorted(set(contexts))
            or not set(contexts) <= _BUNDLE_CONTEXTS
            or not parameters
            or parameters != sorted(set(parameters))
            or dependencies != sorted(set(dependencies))
            or not str(row.get("shared_grammar") or "")
            or not str(row.get("expected_downstream_closure") or "")
            or not exclusions
            or exclusions != sorted(set(exclusions))
            or measurement_status not in _BUNDLE_MEASUREMENT_STATUSES
        ):
            raise WorkSelectionBundleError(
                "Candidate bundles require closed identities, owners, contexts, "
                "grammar, parameters, dependencies, and exclusions"
            )
        _validate_measurement_probe(
            row,
            measurement_status=measurement_status,
        )
        for field in (
            "estimated_implementation_hours",
            "estimated_probe_hours",
            "estimated_generation_hours",
        ):
            if _nonnegative_int(row.get(field), field) < 1:
                raise WorkSelectionBundleError(
                    "Bundle cycle hours must be positive"
                )
        seen.add(bundle_id)
    return bundles, weights


def coverage_scores(
    *,
    complete_cards: int,
    exact_abilities: int,
    residuals: int,
    one_additional: int,
    two_additional: int,
    implementation_hours: int,
    generation_hours: int,
    weights: Mapping[str, int],
) -> tuple[int, float, float]:
    cycle_hours = implementation_hours + generation_hours
    normalized_value = (
        complete_cards * int(weights["complete_card"])
        + exact_abilities * int(weights["exact_ability"])
        + residuals * int(weights["material_residual"])
        + one_additional * int(weights["one_additional_blocker_card"])
        + two_additional * int(weights["two_additional_blocker_card"])
    )
    return (
        cycle_hours,
        round(complete_cards / cycle_hours, 6),
        round(normalized_value / cycle_hours, 6),
    )


def bundle_measurement_decision(
    declared_status: str,
    bounded_verified: bool,
    measurement_outcome: Mapping[str, Any] | None = None,
) -> tuple[str, str | None]:
    if (
        declared_status == "generated_probe"
        and measurement_outcome is not None
        and measurement_outcome.get("decision") == "retired_below_harvest_floor"
    ):
        return (
            "measured_nonviable",
            "The generated current-frontier bounded cohort is below every "
            "implementation harvest floor and remains retired without granting "
            "gameplay trust.",
        )
    if declared_status == "generated_probe" and measurement_outcome is not None:
        return "bounded_executable", None
    if declared_status == "generated_probe":
        return (
            "upper_bound_only",
            "The current frontier lacks a generated bounded cohort measurement; "
            "materialize it before implementation eligibility.",
        )
    if declared_status == "bounded_executable" and bounded_verified:
        return "bounded_executable", None
    if declared_status == "bounded_executable":
        return (
            "upper_bound_only",
            "The declared bounded executable census no longer matches the "
            "generated frontier; a new bounded cohort is required before this "
            "bundle can become foreground.",
        )
    return (
        "upper_bound_only",
        "The synthesized family closure is only an upper bound; declared "
        "exclusions and sibling grammar require a bounded executable cohort "
        "before this bundle can become foreground.",
    )


def estimated_bundle_effort(implementation_hours: int) -> str:
    if implementation_hours <= 8:
        return "small"
    if implementation_hours <= 20:
        return "medium"
    return "large"


def atomic_frontier_bundle(
    *,
    candidate_id: str,
    family_id: str,
    base_family: str,
    effort: str,
    complete_cards: int,
    exact_abilities: int,
    residuals: int,
    one_additional: int,
    two_additional: int,
    weights: Mapping[str, int],
) -> dict[str, Any]:
    implementation_hours = _EFFORT_HOURS.get(effort, _EFFORT_HOURS["unknown"])
    cycle_hours, cards_per_hour, value_per_hour = coverage_scores(
        complete_cards=complete_cards,
        exact_abilities=exact_abilities,
        residuals=residuals,
        one_additional=one_additional,
        two_additional=two_additional,
        implementation_hours=implementation_hours,
        generation_hours=1,
        weights=weights,
    )
    detail = family_id.split(":", 1)[-1]
    return {
        "bundle_id": candidate_id,
        "member_family_ids": [family_id],
        "canonical_owner_ids": [],
        "source_contexts": _SOURCE_CONTEXTS_BY_BASE.get(base_family, []),
        "normalized_literal_parameters": [detail],
        "shared_dependencies": [],
        "shared_grammar": detail,
        "estimated_implementation_hours": implementation_hours,
        "estimated_probe_hours": None,
        "estimated_generation_hours": 1,
        "estimated_cycle_hours": cycle_hours,
        "predicted_complete_cards_per_cycle_hour": cards_per_hour,
        "predicted_normalized_value_per_cycle_hour": value_per_hour,
        "expected_downstream_closure": {
            "one_additional_blocker_cards": one_additional,
            "two_additional_blocker_cards": two_additional,
        },
        "explicit_exclusions": [],
        "measurement_status": "atomic_frontier",
        "synthesized": False,
    }


def _bundle_frontier_gains(
    cards: Sequence[Mapping[str, Any]], member_family_ids: set[str]
) -> dict[str, int]:
    result = {
        "affected_cards": 0,
        "exact_cards": 0,
        "exact_abilities": 0,
        "material_residuals": 0,
        "one_additional_blocker_cards": 0,
        "two_additional_blocker_cards": 0,
    }
    for card in cards:
        blockers = set(card.get("minimum_known_blocker_set", []))
        if blockers & member_family_ids:
            result["affected_cards"] += 1
            remaining = blockers - member_family_ids
            if not remaining:
                result["exact_cards"] += 1
            elif len(remaining) == 1:
                result["one_additional_blocker_cards"] += 1
            elif len(remaining) == 2:
                result["two_additional_blocker_cards"] += 1
        for ability in card.get("abilities", []):
            ability_blockers = set(
                ability.get("blockers", {}).get("canonical_family_ids", [])
            )
            if ability_blockers and ability_blockers <= member_family_ids:
                result["exact_abilities"] += 1
            for residual in ability.get("residuals", []):
                residual_blockers = set(residual.get("family_ids", []))
                if residual_blockers and residual_blockers <= member_family_ids:
                    result["material_residuals"] += 1
    return result


def _ability_references_bundle(
    ability: Mapping[str, Any], member_family_ids: set[str]
) -> bool:
    if (
        set(
            ability.get("blockers", {}).get(
                "canonical_family_ids", []
            )
        )
        & member_family_ids
    ):
        return True
    return any(
        set(residual.get("family_ids", [])) & member_family_ids
        for residual in ability.get("residuals", [])
    )


def _measurement_card_projection(
    card: Mapping[str, Any], member_family_ids: set[str]
) -> dict[str, Any] | None:
    relevant_abilities = [
        ability
        for ability in card.get("abilities", [])
        if _ability_references_bundle(ability, member_family_ids)
    ]
    if (
        not relevant_abilities
        and not set(card.get("minimum_known_blocker_set", []))
        & member_family_ids
    ):
        return None
    return {
        "oracle_id": card.get("oracle_id"),
        "card_name": card.get("card_name"),
        "hard_construction_failure": card.get("hard_construction_failure"),
        "minimum_known_blocker_set": card.get(
            "minimum_known_blocker_set", []
        ),
        "abilities": relevant_abilities,
    }


def bundle_measurement_fingerprint(
    frontier: Mapping[str, Any], bundle_policy: Mapping[str, Any]
) -> str:
    """Bind a bounded probe to its relevant frontier cohort and grammar."""
    member_ids = sorted(
        str(value) for value in bundle_policy["member_family_ids"]
    )
    member_id_set = set(member_ids)
    family_rows = {
        str(row.get("family_id") or ""): row
        for row in frontier.get("family_candidates", [])
    }
    cards = frontier.get("cards")
    if not isinstance(cards, list):
        raise WorkSelectionBundleError(
            "Card frontier lacks complete bundle card rows"
        )
    relevant_cards = sorted(
        (
            projected
            for card in cards
            if (
                projected := _measurement_card_projection(
                    card, member_id_set
                )
            )
            is not None
        ),
        key=lambda card: (
            str(card.get("oracle_id") or ""),
            stable_json(card),
        ),
    )
    payload = {
        "schema_version": _MEASUREMENT_FINGERPRINT_SCHEMA,
        "measurement_method": _MEASUREMENT_METHOD,
        "frontier_contract": {
            field: frontier.get(field)
            for field in (
                "schema_version",
                "algorithm_version",
                "boundary",
                "profile",
                "commander_legal_only",
                "complete_snapshot_claimed",
            )
        },
        "cohort_boundary": {
            field: bundle_policy.get(field)
            for field in (
                "bundle_id",
                "member_family_ids",
                "canonical_owner_ids",
                "source_contexts",
                "normalized_literal_parameters",
                "shared_dependencies",
                "shared_grammar",
                "explicit_exclusions",
                "measurement_probe_id",
            )
        },
        "family_rows": [family_rows.get(member_id) for member_id in member_ids],
        "cards": relevant_cards,
    }
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def candidate_frontier_measurements(
    frontier: Mapping[str, Any],
    bundle_policies: Sequence[Mapping[str, Any]],
    weights: Mapping[str, int],
    cohort_measurements: Mapping[str, Mapping[str, Any]],
    *,
    completed_bundle_ids: Collection[str] = (),
) -> list[dict[str, Any]]:
    family_rows = {
        str(row.get("family_id") or ""): row
        for row in frontier.get("family_candidates", [])
    }
    cards = frontier.get("cards")
    if not isinstance(cards, list):
        raise WorkSelectionBundleError(
            "Card frontier lacks complete bundle card rows"
        )
    result = []
    completed = set(completed_bundle_ids)
    for bundle_policy in bundle_policies:
        bundle_id = str(bundle_policy["bundle_id"])
        if bundle_id in completed:
            continue
        member_ids = [str(value) for value in bundle_policy["member_family_ids"]]
        missing = sorted(set(member_ids) - set(family_rows))
        if len(missing) == len(member_ids):
            continue
        if missing:
            raise WorkSelectionBundleError(
                f"Candidate bundle {bundle_id} references missing families: "
                + ", ".join(missing)
            )
        members = [family_rows[value] for value in member_ids]
        frontier_gains = _bundle_frontier_gains(cards, set(member_ids))
        outcome = cohort_measurements.get(bundle_id)
        cohort_fingerprint = bundle_measurement_fingerprint(
            frontier, bundle_policy
        )
        measurement_outcome_current = bool(
            isinstance(outcome, Mapping)
            and outcome.get("cohort_fingerprint") == cohort_fingerprint
            and outcome.get("probe_id")
            == bundle_policy.get("measurement_probe_id")
        )
        gains = (
            {
                "affected_cards": int(outcome["affected_commander_cards"]),
                "exact_cards": int(outcome["complete_card_gain"]),
                "exact_abilities": int(outcome["exact_ability_gain"]),
                "material_residuals": int(
                    outcome["material_residual_reduction"]
                ),
                "one_additional_blocker_cards": int(
                    outcome["one_additional_blocker_cards"]
                ),
                "two_additional_blocker_cards": int(
                    outcome["two_additional_blocker_cards"]
                ),
            }
            if measurement_outcome_current
            else frontier_gains
        )
        lowerable_occurrences = sum(
            int(row.get("lowerable_untrusted_abilities") or 0)
            for row in members
        )
        total_occurrences = sum(
            int(row.get("occurrences") or 0) for row in members
        )
        bounded_executable_verified = bool(
            (
                measurement_outcome_current
                and outcome.get("decision") == "bounded_executable"
            )
            or (
                lowerable_occurrences
                and lowerable_occurrences == total_occurrences
                and gains["exact_abilities"] == lowerable_occurrences
                and gains["material_residuals"] >= lowerable_occurrences
            )
        )
        prerequisites = sorted(
            {
                str(value)
                for row in members
                for value in row.get("prerequisites", [])
            }
        )
        implementation_hours = int(
            bundle_policy["estimated_implementation_hours"]
        )
        generation_hours = int(bundle_policy["estimated_generation_hours"])
        cycle_hours, cards_per_hour, value_per_hour = coverage_scores(
            complete_cards=gains["exact_cards"],
            exact_abilities=gains["exact_abilities"],
            residuals=gains["material_residuals"],
            one_additional=gains["one_additional_blocker_cards"],
            two_additional=gains["two_additional_blocker_cards"],
            implementation_hours=implementation_hours,
            generation_hours=generation_hours,
            weights=weights,
        )
        result.append(
            {
                "policy": bundle_policy,
                "member_ids": member_ids,
                "members": members,
                "gains": gains,
                "prerequisites": prerequisites,
                "implementation_hours": implementation_hours,
                "generation_hours": generation_hours,
                "cycle_hours": cycle_hours,
                "cards_per_hour": cards_per_hour,
                "value_per_hour": value_per_hour,
                "bounded_executable_verified": (
                    bounded_executable_verified
                ),
                "measurement_outcome_current": measurement_outcome_current,
                "measurement_cohort_fingerprint": cohort_fingerprint,
                "measurement_outcome": (
                    dict(outcome) if measurement_outcome_current else None
                ),
                "interaction_risks": sorted(
                    {
                        str(row.get("interaction_risk") or "unknown")
                        for row in members
                    }
                ),
            }
        )
    return result


def validated_candidate_frontier_measurements(
    frontier: Mapping[str, Any],
    bundle_policies: Sequence[Mapping[str, Any]],
    weights: Mapping[str, int],
    cohort_measurement_artifact: Mapping[str, Any],
    coverage: Mapping[str, Any],
    *,
    completed_bundle_ids: Collection[str] = (),
) -> list[dict[str, Any]]:
    fingerprints = {
        str(bundle["bundle_id"]): bundle_measurement_fingerprint(
            frontier, bundle
        )
        for bundle in bundle_policies
        if bundle.get("measurement_probe_id") is not None
    }
    try:
        measurements = validate_work_selection_cohort_measurements(
            cohort_measurement_artifact,
            frontier=frontier,
            bundle_policies=bundle_policies,
            cohort_fingerprints=fingerprints,
            coverage=coverage,
        )
    except WorkSelectionCohortMeasurementError as exc:
        raise WorkSelectionBundleError(str(exc)) from exc
    return candidate_frontier_measurements(
        frontier,
        bundle_policies,
        weights,
        measurements,
        completed_bundle_ids=completed_bundle_ids,
    )


__all__ = [
    "atomic_frontier_bundle",
    "bundle_measurement_fingerprint",
    "bundle_measurement_decision",
    "candidate_frontier_measurements",
    "estimated_bundle_effort",
    "single_candidate_bundle",
    "validated_candidate_frontier_measurements",
    "validate_bundle_policy",
    "WorkSelectionBundleError",
]
