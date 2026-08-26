from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Mapping

from .work_selection_common import (
    WorkSelectionError,
    mapping,
    nonnegative_int,
    stable_hash,
)


_HISTORY_FIELDS = {
    "schema_version",
    "algorithm_version",
    "entries",
    "outcome_basis",
    "structural_carrier_limitation",
    "semantic_outcome_status",
    "pending_transition",
    "fingerprint",
}
_PENDING_FIELDS = {
    "transition_id",
    "bundle_id",
    "candidate_ids",
    "family_ids",
    "capability_ids",
    "expected_complete_card_gain",
    "non_harvest_reason",
    "outcome_kind",
    "compiler_version",
    "card_program_schema_version",
    "semantic_receipt_sha256",
    "support_counts",
    "grants_gameplay_trust",
    "resolution",
}
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
_ENTRY_FIELDS = {
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


def _validated_history_header(
    value: Mapping[str, Any],
) -> tuple[list[Any], str, Mapping[str, Any] | None]:
    if set(value) != _HISTORY_FIELDS or int(value.get("schema_version") or 0) != 2:
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
        set(pending) != _PENDING_FIELDS
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
        and (expected is None or (type(expected) is int and expected >= 0))
    )
    reason = pending.get("non_harvest_reason")
    is_non_harvest = bool(
        pending.get("bundle_id") is None
        and not candidates
        and not families
        and not capabilities
        and expected is None
        and isinstance(reason, str)
        and len(reason.strip()) >= 20
    )
    kind = pending["outcome_kind"]
    if (kind == "harvest") != is_harvest or (kind == "non_harvest") != is_non_harvest:
        raise WorkSelectionError(
            "Generated harvest pending outcome kind is inconsistent"
        )


def _validate_history_entries(history: list[Any]) -> None:
    ids: set[str] = set()
    for index, raw in enumerate(history):
        row = mapping(raw, f"harvest_outcome_history[{index}]")
        if set(row) != _ENTRY_FIELDS:
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
        expected_gain = row.get("expected_complete_card_gain")
        if expected_gain is not None:
            nonnegative_int(expected_gain, "expected_complete_card_gain")
        basis = row.get("expected_complete_card_gain_basis")
        if basis not in {"authoritative_source", "not_captured"} or (
            expected_gain is None
        ) != (basis == "not_captured"):
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
