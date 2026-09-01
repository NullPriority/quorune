from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .work_selection_common import (
    WorkSelectionError,
    mapping,
    nonnegative_int,
    stable_hash,
)


_HISTORY_FIELDS = {
    "schema_version",
    "algorithm_version",
    "legacy_provenance_fingerprint",
    "entries",
    "outcome_basis",
    "structural_carrier_limitation",
    "semantic_outcome_status",
    "pending_transition",
    "fingerprint",
}
_PENDING_COMMON_FIELDS = {
    "transition_id",
    "bundle_id",
    "candidate_ids",
    "family_ids",
    "capability_ids",
    "non_harvest_reason",
    "outcome_kind",
    "compiler_version",
    "card_program_schema_version",
    "semantic_receipt_sha256",
    "support_counts",
    "grants_gameplay_trust",
    "resolution",
}
_PENDING_LEGACY_FIELDS = _PENDING_COMMON_FIELDS | {
    "expected_complete_card_gain"
}
_PENDING_MEASURED_FIELDS = _PENDING_COMMON_FIELDS | {"measurement_id"}
_RECEIPT_PATHS = {
    "coverage/card-program-coverage-commander.json",
    "coverage/oracle-coverage-commander.json",
    "coverage/card-unlock-frontier.json.gz",
}
_SUPPORT_COUNT_FIELDS = {
    "oracle_exact_cards",
    "trusted_card_programs",
    "capability_closed_card_programs",
    "oracle_material_residuals",
    "card_program_material_residuals",
    "card_program_ability_records",
    "hard_construction_failures",
}
_LEGACY_ENTRY_FIELDS = {
    "bundle_id",
    "candidate_ids",
    "expected_complete_card_gain",
    "expected_complete_card_gain_basis",
    "base_receipt",
    "head_receipt",
    "actual_complete_card_gain",
    "actual_exact_card_gain",
    "actual_trusted_card_gain",
    "actual_capability_closed_card_gain",
    "oracle_status_delta",
    "card_program_status_delta",
    "failed_card_delta",
    "hard_construction_failure_delta",
    "oracle_exact_ability_node_delta",
    "actual_exact_ability_gain",
    "card_program_ability_record_delta",
    "executable_trust_transition_delta",
    "executable_trust_transitions",
    "frontier_ability_carrier_delta",
    "card_program_structural_carrier_reconciliation",
    "actual_material_oracle_residual_reduction",
    "actual_material_card_program_residual_reduction",
    "actual_material_residual_reduction",
    "interaction_assurance_delta",
    "architecture_delta",
}
_CONTENT_ENTRY_FIELDS = _LEGACY_ENTRY_FIELDS | {
    "transition_id",
    "family_ids",
    "capability_ids",
    "receipt_identity_kind",
    "entry_fingerprint",
}
_MEASURED_CONTENT_ENTRY_FIELDS = _CONTENT_ENTRY_FIELDS | {
    "measurement_id",
    "measurement_probe_id",
    "measurement_receipt_fingerprint",
    "measurement_frontier_fingerprint",
}
_FORECAST_CORRECTION_FIELDS = {
    "transition_id",
    "original_expected_complete_card_gain",
    "certified_complete_card_lower_bound",
    "certified_exact_ability_lower_bound",
    "certified_material_residual_reduction_lower_bound",
    "measurement_probe_id",
    "reason",
}
_CORRECTED_CONTENT_ENTRY_FIELDS = _CONTENT_ENTRY_FIELDS | {
    "forecast_correction"
}
_CORRECTED_MEASURED_CONTENT_ENTRY_FIELDS = (
    _MEASURED_CONTENT_ENTRY_FIELDS | {"forecast_correction"}
)
COHORT_MEASUREMENT_SCHEMA_VERSION = 3
COHORT_MEASUREMENT_ALGORITHM_VERSION = "frontier-existing-owner-probe-v3"
_COHORT_DECISIONS = {
    "bounded_executable",
    "retired_below_harvest_floor",
}
_COHORT_ROW_FIELDS = {
    "measurement_id",
    "bundle_id",
    "probe_id",
    "cohort_fingerprint",
    "affected_commander_cards",
    "complete_card_gain",
    "one_additional_blocker_cards",
    "two_additional_blocker_cards",
    "exact_ability_gain",
    "material_residual_reduction",
    "decision",
    "grants_gameplay_trust",
}
_COHORT_ACCOUNTING_FIELD = "candidate_accounting"
_COHORT_ACCOUNTING_FIELDS = {
    "affected_oracle_carriers",
    "existing_exact_sibling_nodes",
    "remaining_residual_sibling_nodes",
    "trusted_program_transitions",
    "unresolved_program_transitions",
    "expected_oracle_residual_reduction",
    "expected_card_program_residual_reduction",
    "newly_applicable_high_risk_pairs",
    "cards_excluded_by_unsupported_sibling",
    "cards_excluded_by_unsupported_prevention_grammar",
}
_TRANSITION_MEASUREMENT_FIELDS = {
    "transition_id",
    "frontier_fingerprint",
    "oracle_source_sha256",
    "measurement",
    "receipt_fingerprint",
}


class WorkSelectionCohortMeasurementError(ValueError):
    pass


def _validate_cohort_row_shape(value: Mapping[str, Any]) -> bool:
    fields = set(value)
    if fields != _COHORT_ROW_FIELDS and fields != _COHORT_ROW_FIELDS | {
        _COHORT_ACCOUNTING_FIELD
    }:
        return False
    accounting = value.get(_COHORT_ACCOUNTING_FIELD)
    if accounting is None:
        return True
    return bool(
        isinstance(accounting, Mapping)
        and set(accounting) == _COHORT_ACCOUNTING_FIELDS
        and all(
            type(accounting.get(field)) is int and accounting[field] >= 0
            for field in _COHORT_ACCOUNTING_FIELDS
        )
    )


def validate_harvest_forecast_correction(
    value: Any,
    *,
    outcome: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FORECAST_CORRECTION_FIELDS:
        raise WorkSelectionError(
            "Content-bound harvest forecast correction is invalid"
        )
    correction = dict(value)
    transition_id = str(correction.get("transition_id") or "")
    measurement_probe_id = str(correction.get("measurement_probe_id") or "")
    reason = str(correction.get("reason") or "").strip()
    integer_fields = (
        "original_expected_complete_card_gain",
        "certified_complete_card_lower_bound",
        "certified_exact_ability_lower_bound",
        "certified_material_residual_reduction_lower_bound",
    )
    if (
        not transition_id
        or not measurement_probe_id
        or len(reason) < 40
        or any(
            type(correction.get(field)) is not int or correction[field] < 0
            for field in integer_fields
        )
        or correction["certified_complete_card_lower_bound"]
        > correction["original_expected_complete_card_gain"]
    ):
        raise WorkSelectionError(
            "Content-bound harvest forecast correction is invalid"
        )
    correction["transition_id"] = transition_id
    correction["measurement_probe_id"] = measurement_probe_id
    correction["reason"] = reason
    if outcome is None:
        return correction
    if (
        transition_id != outcome.get("transition_id")
        or correction["original_expected_complete_card_gain"]
        != outcome.get("expected_complete_card_gain")
        or type(outcome.get("actual_complete_card_gain")) is not int
        or outcome["actual_complete_card_gain"]
        < correction["certified_complete_card_lower_bound"]
        or type(outcome.get("actual_exact_ability_gain")) is not int
        or outcome["actual_exact_ability_gain"]
        < correction["certified_exact_ability_lower_bound"]
        or type(outcome.get("actual_material_residual_reduction")) is not int
        or outcome["actual_material_residual_reduction"]
        < correction["certified_material_residual_reduction_lower_bound"]
        or (
            correction["certified_complete_card_lower_bound"]
            == correction["original_expected_complete_card_gain"]
            and measurement_probe_id == outcome.get("measurement_probe_id")
        )
    ):
        raise WorkSelectionError(
            "Content-bound harvest forecast correction is invalid"
        )
    return correction


def _validate_transition_measurements(
    rows: Sequence[Any],
    *,
    expected_bundles: Mapping[str, str],
    metric_fields: Sequence[str],
) -> None:
    seen_transitions: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping) or set(raw) != _TRANSITION_MEASUREMENT_FIELDS:
            raise WorkSelectionCohortMeasurementError(
                "Transition cohort measurement is malformed"
            )
        receipt = dict(raw)
        receipt_fingerprint = receipt.pop("receipt_fingerprint", None)
        transition_id = str(raw.get("transition_id") or "")
        measurement = raw.get("measurement")
        if (
            not transition_id
            or transition_id in seen_transitions
            or receipt_fingerprint != stable_hash(receipt)
            or not str(raw.get("frontier_fingerprint") or "")
            or not str(raw.get("oracle_source_sha256") or "")
            or not isinstance(measurement, Mapping)
            or not _validate_cohort_row_shape(measurement)
            or not str(measurement.get("cohort_fingerprint") or "")
            or any(
                type(measurement.get(field)) is not int
                or measurement[field] < 0
                for field in metric_fields
            )
            or measurement.get("decision") != "bounded_executable"
            or measurement.get("grants_gameplay_trust") is not False
            or measurement.get("bundle_id") not in expected_bundles
            or not str(measurement.get("probe_id") or "")
            or measurement.get("measurement_id")
            != "measurement:"
            + str(measurement.get("bundle_id") or "").split(":", 1)[-1]
        ):
            raise WorkSelectionCohortMeasurementError(
                "Transition cohort measurement identity is invalid"
            )
        seen_transitions.add(transition_id)


def validate_work_selection_cohort_measurements(
    value: Any,
    *,
    frontier: Mapping[str, Any],
    bundle_policies: Sequence[Mapping[str, Any]],
    cohort_fingerprints: Mapping[str, str],
    coverage: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise WorkSelectionCohortMeasurementError(
            "Cohort measurements must be an object"
        )
    expected_fields = {
        "schema_version",
        "algorithm_version",
        "frontier_fingerprint",
        "oracle_source_sha256",
        "measurements",
        "transition_measurements",
        "fingerprint",
    }
    unsigned = dict(value)
    fingerprint = unsigned.pop("fingerprint", None)
    if (
        set(value) != expected_fields
        or value.get("schema_version") != COHORT_MEASUREMENT_SCHEMA_VERSION
        or value.get("algorithm_version")
        != COHORT_MEASUREMENT_ALGORITHM_VERSION
        or fingerprint != stable_hash(unsigned)
    ):
        raise WorkSelectionCohortMeasurementError(
            "Cohort measurement artifact is malformed"
        )
    snapshot = frontier.get("card_data_snapshot")
    oracle_source = (
        str(snapshot.get("oracle_source_sha256") or "")
        if isinstance(snapshot, Mapping)
        else ""
    )
    if (
        value.get("frontier_fingerprint") != frontier.get("fingerprint")
        or value.get("oracle_source_sha256") != oracle_source
    ):
        raise WorkSelectionCohortMeasurementError(
            "Cohort measurement artifact is stale"
        )
    measurements = value.get("measurements")
    transition_measurements = value.get("transition_measurements")
    if not isinstance(measurements, list) or not isinstance(
        transition_measurements, list
    ):
        raise WorkSelectionCohortMeasurementError(
            "Cohort measurements and transition receipts must be arrays"
        )
    expected_bundles = {
        str(bundle["bundle_id"]): str(bundle["measurement_probe_id"])
        for bundle in bundle_policies
        if bundle.get("measurement_probe_id") is not None
    }
    metric_fields = (
        "affected_commander_cards",
        "complete_card_gain",
        "one_additional_blocker_cards",
        "two_additional_blocker_cards",
        "exact_ability_gain",
        "material_residual_reduction",
    )
    result: dict[str, Mapping[str, Any]] = {}
    for row in measurements:
        if not isinstance(row, Mapping) or not _validate_cohort_row_shape(row):
            raise WorkSelectionCohortMeasurementError(
                "Cohort measurement row is malformed"
            )
        bundle_id = str(row.get("bundle_id") or "")
        if (
            bundle_id in result
            or expected_bundles.get(bundle_id) != row.get("probe_id")
            or row.get("measurement_id")
            != "measurement:" + bundle_id.split(":", 1)[-1]
            or row.get("cohort_fingerprint")
            != cohort_fingerprints.get(bundle_id)
            or row.get("decision") not in _COHORT_DECISIONS
            or row.get("grants_gameplay_trust") is not False
            or any(
                type(row.get(field)) is not int or row[field] < 0
                for field in metric_fields
            )
        ):
            raise WorkSelectionCohortMeasurementError(
                "Cohort measurement identity or metric is invalid"
            )
        reaches_floor = (
            row["complete_card_gain"]
            >= int(coverage["minimum_complete_card_gain"])
            or row["exact_ability_gain"]
            >= int(coverage["minimum_exact_ability_gain"])
            or row["material_residual_reduction"]
            >= int(coverage["minimum_material_residual_reduction"])
        )
        expected_decision = (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        )
        if row["decision"] != expected_decision:
            raise WorkSelectionCohortMeasurementError(
                "Cohort measurement decision contradicts the harvest floors"
            )
        result[bundle_id] = row
    if set(result) != set(expected_bundles):
        raise WorkSelectionCohortMeasurementError(
            "Cohort measurement inventory is incomplete"
        )
    _validate_transition_measurements(
        transition_measurements,
        expected_bundles=expected_bundles,
        metric_fields=metric_fields,
    )
    return result


def work_selection_source_fingerprints(
    inputs: Mapping[str, Any],
) -> dict[str, str]:
    return {
        "architecture_audit": stable_hash(inputs["architecture_audit"]),
        "card_unlock_frontier": str(
            inputs["card_unlock_frontier"].get("fingerprint") or ""
        ),
        "cohort_measurements": str(
            inputs["cohort_measurements"].get("fingerprint") or ""
        ),
        "harvest_outcome_history": str(
            inputs["harvest_outcome_history"].get("fingerprint") or ""
        ),
        "compact_ci_dependencies": stable_hash(
            inputs["compact_ci_dependencies"]
        ),
        "platform_readiness": stable_hash(inputs["platform_readiness"]),
        "reusable_piece_delta": str(
            inputs["reusable_piece_delta"].get("fingerprint") or ""
        ),
        "reusable_piece_interactions": str(
            inputs["reusable_piece_interactions"].get("fingerprint") or ""
        ),
    }


def _validated_history_header(
    value: Mapping[str, Any],
) -> tuple[list[Any], str, Mapping[str, Any] | None]:
    if set(value) != _HISTORY_FIELDS or int(value.get("schema_version") or 0) != 3:
        raise WorkSelectionError("Generated harvest history has an invalid shape")
    fingerprint_payload = dict(value)
    fingerprint = str(fingerprint_payload.pop("fingerprint") or "")
    if fingerprint != stable_hash(fingerprint_payload):
        raise WorkSelectionError("Generated harvest history fingerprint is stale")
    history = list(value.get("entries", []))
    status = str(value.get("semantic_outcome_status") or "")
    pending = value.get("pending_transition")
    if (
        status not in {"current", "pending"}
        or (status == "current" and pending is not None)
        or (status == "pending" and not isinstance(pending, Mapping))
    ):
        raise WorkSelectionError(
            "Generated harvest history semantic outcome status is invalid"
        )
    return history, status, pending if isinstance(pending, Mapping) else None


def _validated_identity_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise WorkSelectionError(label)
    return [str(item) for item in value]


def _validate_pending_transition(pending: Mapping[str, Any]) -> None:
    hashes = pending.get("semantic_receipt_sha256")
    support_counts = pending.get("support_counts")
    if (
        set(pending) not in (
            _PENDING_LEGACY_FIELDS,
            _PENDING_MEASURED_FIELDS,
        )
        or not str(pending.get("transition_id") or "")
        or pending.get("outcome_kind") not in {"harvest", "non_harvest"}
        or pending.get("grants_gameplay_trust") is not False
        or not str(pending.get("resolution") or "")
        or not isinstance(hashes, Mapping)
        or set(hashes) != _RECEIPT_PATHS
        or any(not str(value) for value in hashes.values())
        or not isinstance(support_counts, Mapping)
        or set(support_counts) != _SUPPORT_COUNT_FIELDS
        or any(
            type(count) is not int or count < 0
            for count in support_counts.values()
        )
    ):
        raise WorkSelectionError(
            "Generated harvest history pending transition is incomplete"
        )
    candidates = _validated_identity_list(
        pending.get("candidate_ids"),
        "Generated harvest history pending candidates are invalid",
    )
    families = _validated_identity_list(
        pending.get("family_ids"),
        "Generated harvest pending family and capability IDs are invalid",
    )
    capabilities = _validated_identity_list(
        pending.get("capability_ids"),
        "Generated harvest pending family and capability IDs are invalid",
    )
    expected = pending.get("expected_complete_card_gain")
    measurement_id = pending.get("measurement_id")
    is_harvest = bool(
        isinstance(pending.get("bundle_id"), str)
        and str(pending["bundle_id"]).startswith("bundle:")
        and candidates
        and candidates == sorted(set(candidates))
        and families
        and families == sorted(set(families))
        and capabilities
        and capabilities == sorted(set(capabilities))
        and pending.get("non_harvest_reason") is None
        and (
            (
                set(pending) == _PENDING_LEGACY_FIELDS
                and (
                    expected is None
                    or (type(expected) is int and expected >= 0)
                )
            )
            or (
                set(pending) == _PENDING_MEASURED_FIELDS
                and measurement_id
                == "measurement:"
                + str(pending.get("bundle_id") or "").split(":", 1)[-1]
            )
        )
    )
    reason = pending.get("non_harvest_reason")
    is_non_harvest = bool(
        set(pending) == _PENDING_LEGACY_FIELDS
        and pending.get("bundle_id") is None
        and not candidates
        and not families
        and not capabilities
        and expected is None
        and measurement_id is None
        and isinstance(reason, str)
        and len(reason.strip()) >= 20
    )
    kind = pending["outcome_kind"]
    if (kind == "harvest") != is_harvest or (kind == "non_harvest") != is_non_harvest:
        raise WorkSelectionError(
            "Generated harvest pending outcome kind is inconsistent"
        )


def _validate_content_history_entry(
    row: Mapping[str, Any], fields: set[str]
) -> None:
    if fields == _LEGACY_ENTRY_FIELDS:
        return
    unsigned = dict(row)
    entry_fingerprint = str(unsigned.pop("entry_fingerprint") or "")
    families = _validated_identity_list(
        row.get("family_ids"),
        "Content-bound harvest family IDs are invalid",
    )
    capabilities = _validated_identity_list(
        row.get("capability_ids"),
        "Content-bound harvest capability IDs are invalid",
    )
    base_receipt = row["base_receipt"]
    head_receipt = row["head_receipt"]
    if (
        entry_fingerprint != stable_hash(unsigned)
        or row.get("receipt_identity_kind") != "semantic_content"
        or not str(row.get("transition_id") or "")
        or not families
        or families != sorted(set(families))
        or not capabilities
        or capabilities != sorted(set(capabilities))
        or "commit" in base_receipt
        or "commit" in head_receipt
        or not str(base_receipt.get("content_fingerprint") or "")
        or not str(head_receipt.get("content_fingerprint") or "")
    ):
        raise WorkSelectionError(
            "Content-bound harvest outcome identity is invalid"
        )
    if fields not in (
        _CORRECTED_CONTENT_ENTRY_FIELDS,
        _CORRECTED_MEASURED_CONTENT_ENTRY_FIELDS,
    ):
        return
    validate_harvest_forecast_correction(
        row.get("forecast_correction"), outcome=row
    )


def _validate_history_entries(history: list[Any]) -> None:
    ids: set[str] = set()
    valid_fields = (
        _LEGACY_ENTRY_FIELDS,
        _CONTENT_ENTRY_FIELDS,
        _CORRECTED_CONTENT_ENTRY_FIELDS,
        _MEASURED_CONTENT_ENTRY_FIELDS,
        _CORRECTED_MEASURED_CONTENT_ENTRY_FIELDS,
    )
    for index, raw in enumerate(history):
        row = mapping(raw, f"harvest_outcome_history[{index}]")
        fields = set(row)
        if fields not in valid_fields:
            raise WorkSelectionError(
                "Harvest outcome history entries have an invalid shape"
            )
        bundle_id = str(row.get("bundle_id") or "")
        candidate_ids = [str(value) for value in row.get("candidate_ids", [])]
        if (
            not bundle_id.startswith("bundle:")
            or bundle_id in ids
            or not candidate_ids
            or candidate_ids != sorted(set(candidate_ids))
            or not isinstance(row.get("base_receipt"), Mapping)
            or not isinstance(row.get("head_receipt"), Mapping)
        ):
            raise WorkSelectionError(
                "Harvest outcome history bundle identities must be unique"
            )
        ids.add(bundle_id)
        _validate_content_history_entry(row, fields)
        expected_gain = row.get("expected_complete_card_gain")
        if expected_gain is not None:
            nonnegative_int(expected_gain, "expected_complete_card_gain")
        basis = row.get("expected_complete_card_gain_basis")
        measured_entry = fields in (
            _MEASURED_CONTENT_ENTRY_FIELDS,
            _CORRECTED_MEASURED_CONTENT_ENTRY_FIELDS,
        )
        if (
            basis
            not in {
                "authoritative_source",
                "generated_transition_cohort",
                "not_captured",
            }
            or (expected_gain is None) != (basis == "not_captured")
            or (basis == "generated_transition_cohort") != measured_entry
            or (
                measured_entry
                and (
                    not str(row.get("measurement_id") or "")
                    or not str(row.get("measurement_probe_id") or "")
                    or not str(
                        row.get("measurement_receipt_fingerprint") or ""
                    )
                    or not str(
                        row.get("measurement_frontier_fingerprint") or ""
                    )
                )
            )
        ):
            raise WorkSelectionError(
                "Harvest outcome expected-gain basis is inconsistent"
            )
        for field in (
            "actual_complete_card_gain",
            "actual_exact_ability_gain",
            "actual_material_residual_reduction",
        ):
            if type(row.get(field)) is not int:
                raise WorkSelectionError(f"{field} must be an integer")
        reconciliation = mapping(
            row.get("card_program_structural_carrier_reconciliation"),
            "card_program_structural_carrier_reconciliation",
        )
        if (
            reconciliation.get("availability") != "aggregate_only"
            or any(
                reconciliation.get(field) is not None
                for field in ("additions", "removals", "reclassifications")
            )
            or not str(reconciliation.get("reason") or "")
        ):
            raise WorkSelectionError(
                "Historical structural carriers must remain explicitly unreconciled"
            )


def _history_summary(history: list[Any], minimum_gain: int) -> dict[str, Any]:
    consecutive_subthreshold = 0
    for row in reversed(history):
        if int(row["actual_complete_card_gain"]) >= minimum_gain:
            break
        consecutive_subthreshold += 1
    return {
        "harvest_outcome_history": history,
        "consecutive_subthreshold_harvests": consecutive_subthreshold,
        "subthreshold_harvests": sum(
            int(row["actual_complete_card_gain"]) < minimum_gain for row in history
        ),
        "card_gain_absolute_error": sum(
            abs(
                int(row["expected_complete_card_gain"])
                - int(row["actual_complete_card_gain"])
            )
            for row in history
            if row["expected_complete_card_gain"] is not None
        ),
        "known_expected_harvests": sum(
            row["expected_complete_card_gain"] is not None for row in history
        ),
    }


def validate_harvest_history(
    value: Mapping[str, Any], *, minimum_gain: int
) -> dict[str, Any]:
    history, status, pending = _validated_history_header(value)
    if status == "pending":
        assert pending is not None
        _validate_pending_transition(pending)
    _validate_history_entries(history)
    return {
        **_history_summary(history, minimum_gain),
        "semantic_outcome_status": status,
        "pending_transition": pending,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise WorkSelectionError(f"Missing work-selection input: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkSelectionError(f"Invalid work-selection input: {path}") from exc
    if not isinstance(value, dict):
        raise WorkSelectionError(f"Work-selection input must be an object: {path}")
    return value


def _read_gzip_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise WorkSelectionError(f"Missing work-selection input: {path}")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkSelectionError(f"Invalid work-selection input: {path}") from exc
    if not isinstance(value, dict):
        raise WorkSelectionError(f"Work-selection input must be an object: {path}")
    return value


def load_work_selection_inputs(
    root: str | Path,
    *,
    harvest_outcome_history: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    repository = Path(root)
    return {
        "architecture_audit": _read_json(
            repository / "coverage" / "architecture-audit.json"
        ),
        "card_unlock_frontier": _read_gzip_json(
            repository / "coverage" / "card-unlock-frontier.json.gz"
        ),
        "cohort_measurements": _read_json(
            repository
            / "coverage"
            / "work-selection-cohort-measurements.json"
        ),
        "harvest_outcome_history": (
            dict(harvest_outcome_history)
            if harvest_outcome_history is not None
            else _read_json(repository / "coverage" / "harvest-outcome-history.json")
        ),
        "compact_ci_dependencies": _read_json(
            repository / "coverage" / "compact-ci-card-dependencies.json"
        ),
        "platform_readiness": _read_json(
            repository / "coverage" / "platform-readiness.json"
        ),
        "reusable_piece_delta": _read_json(
            repository / "coverage" / "reusable-piece-delta.json"
        ),
        "reusable_piece_interactions": _read_gzip_json(
            repository / "coverage" / "reusable-piece-interactions.json.gz"
        ),
    }
