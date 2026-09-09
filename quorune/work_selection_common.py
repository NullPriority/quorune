from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from .util import stable_json


class WorkSelectionError(ValueError):
    pass


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkSelectionError(f"{label} must be an object")
    return value


def nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise WorkSelectionError(f"{label} must be a nonnegative integer")
    return value


def prerequisite_fanout_identity(
    measurement_outcome: Any,
) -> tuple[str | None, int | None]:
    if not isinstance(measurement_outcome, Mapping):
        return None, None
    fanout = measurement_outcome.get("prerequisite_fanout")
    gain = (
        fanout.get("downstream_complete_card_gain")
        if isinstance(fanout, Mapping)
        else None
    )
    measurement_id = str(measurement_outcome.get("measurement_id") or "")
    return measurement_id or None, gain if type(gain) is int else None


def prerequisite_exception_is_approved(
    exceptions: Sequence[Mapping[str, Any]],
    *,
    candidate_id: str,
    measurement_id: str | None,
    downstream_gain: int | None,
    minimum_downstream_gain: int,
) -> bool:
    return any(
        str(row["candidate_id"]) == candidate_id
        and (
            "expected_downstream_complete_card_gain" in row
            or (
                str(row.get("measurement_id") or "")
                == str(measurement_id or "")
                and type(downstream_gain) is int
                and downstream_gain >= minimum_downstream_gain
            )
        )
        for row in exceptions
    )


def prerequisite_exception_is_eligible(
    exceptions: Sequence[Mapping[str, Any]],
    *,
    candidate_id: str,
    measurement_id: str | None,
    downstream_gain: int | None,
    complete_gain: int,
    minimum_complete_gain: int,
    minimum_downstream_gain: int,
    consecutive_exceptions: int,
    maximum_consecutive_exceptions: int,
) -> bool:
    return bool(
        prerequisite_exception_is_approved(
            exceptions,
            candidate_id=candidate_id,
            measurement_id=measurement_id,
            downstream_gain=downstream_gain,
            minimum_downstream_gain=minimum_downstream_gain,
        )
        and complete_gain >= minimum_complete_gain
        and consecutive_exceptions < maximum_consecutive_exceptions
    )


def transition_measurement_matches_policy(
    measurement: Mapping[str, Any],
    *,
    bundle: Mapping[str, Any],
    coverage: Mapping[str, Any],
) -> bool:
    complete_gain = int(measurement.get("complete_card_gain") or 0)
    if measurement.get("decision") == "bounded_executable":
        return complete_gain > 0
    measurement_id, downstream_gain = prerequisite_fanout_identity(
        measurement
    )
    return bool(
        bundle.get("measurement_status") == "generated_probe"
        and measurement.get("decision") == "retired_below_harvest_floor"
        and complete_gain
        >= int(coverage["minimum_prerequisite_complete_card_gain"])
        and prerequisite_exception_is_approved(
            coverage["approved_prerequisite_exceptions"],
            candidate_id=str(bundle["bundle_id"]),
            measurement_id=measurement_id,
            downstream_gain=downstream_gain,
            minimum_downstream_gain=int(
                coverage["minimum_prerequisite_downstream_card_gain"]
            ),
        )
    )
