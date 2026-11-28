from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def hidden_information_audit(
    decisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit durable pilot inputs for direct cross-seat private-zone exposure."""

    findings: list[dict[str, Any]] = []
    forbidden_keys = {
        "authoritative_state",
        "checkpoint",
        "initial_checkpoint",
        "library_order",
        "analyst",
    }

    def inspect(
        value: Any,
        *,
        seat: str,
        decision_id: str,
        path: tuple[str, ...] = (),
    ) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key)
                child_path = (*path, key_text)
                if key_text.lower() in forbidden_keys:
                    findings.append(
                        {
                            "decision_id": decision_id,
                            "seat": seat,
                            "path": ".".join(child_path),
                            "reason": "forbidden authoritative or analyst field",
                        }
                    )
                inspect(
                    child,
                    seat=seat,
                    decision_id=decision_id,
                    path=child_path,
                )
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                inspect(
                    child,
                    seat=seat,
                    decision_id=decision_id,
                    path=(*path, str(index)),
                )
            return
        if (
            isinstance(value, str)
            and path
            and path[-1] == "id"
            and any(
                private_key in path
                for private_key in ("hand", "search_cards", "candidates")
            )
            and value
            and not value.startswith(seat)
        ):
            findings.append(
                {
                    "decision_id": decision_id,
                    "seat": seat,
                    "path": ".".join(path),
                    "reason": f"private candidate belongs to another seat: {value}",
                }
            )

    audited = 0
    for row in decisions:
        if row.get("role") != "pilot":
            continue
        seat = str(row.get("seat") or "")
        context = row.get("decision_context")
        if not seat or not isinstance(context, Mapping):
            continue
        audited += 1
        inspect(
            context,
            seat=seat,
            decision_id=str(row.get("decision_id") or ""),
        )
    return {
        "schema_version": 1,
        "source": "durable_pilot_decision_contexts",
        "pilot_rows_audited": audited,
        "seat_projection_verified": not findings,
        "findings": findings,
    }
