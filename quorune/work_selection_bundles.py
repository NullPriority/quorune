from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_BUNDLE_CONTEXTS = {"activated", "modal", "setup", "spell", "triggered"}
_BUNDLE_MEASUREMENT_STATUSES = {
    "bounded_executable",
    "upper_bound_only",
}
_BUNDLE_OWNER = re.compile(r"^(?:capability|component):[A-Za-z0-9_.:-]+$")
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
) -> tuple[str, str | None]:
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


def candidate_frontier_measurements(
    frontier: Mapping[str, Any],
    bundle_policies: Sequence[Mapping[str, Any]],
    weights: Mapping[str, int],
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
    for bundle_policy in bundle_policies:
        bundle_id = str(bundle_policy["bundle_id"])
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
        gains = _bundle_frontier_gains(cards, set(member_ids))
        lowerable_occurrences = sum(
            int(row.get("lowerable_untrusted_abilities") or 0)
            for row in members
        )
        total_occurrences = sum(
            int(row.get("occurrences") or 0) for row in members
        )
        bounded_executable_verified = bool(
            lowerable_occurrences
            and lowerable_occurrences == total_occurrences
            and gains["exact_abilities"] == lowerable_occurrences
            and gains["material_residuals"] >= lowerable_occurrences
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
                "interaction_risks": sorted(
                    {
                        str(row.get("interaction_risk") or "unknown")
                        for row in members
                    }
                ),
            }
        )
    return result


__all__ = [
    "atomic_frontier_bundle",
    "bundle_measurement_decision",
    "candidate_frontier_measurements",
    "single_candidate_bundle",
    "validate_bundle_policy",
    "WorkSelectionBundleError",
]
