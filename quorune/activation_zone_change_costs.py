from __future__ import annotations

"""Closed continuation identity for activation zone-change costs."""

from typing import Any, Mapping

from .additional_cost_vocabulary import FIXED_ZONE_CHANGE_COST_CONTRACTS


def activation_zone_change_cost_reference(
    response: Mapping[str, Any],
    *,
    origin: Any,
    destination: Any,
    object_ref: Any,
) -> str | None:
    """Return the exact cost object pinned by a resumable activation."""

    if (
        type(origin) is not str
        or not origin
        or type(destination) is not str
        or not destination
        or type(object_ref) is not str
        or not object_ref
    ):
        return None
    cost_summary = response.get("cost_summary")
    if not isinstance(cost_summary, Mapping):
        return None
    source_ref = response.get("source")
    source_flags = {
        field
        for field in ("discard_self", "exile_self", "sac_self")
        if field in cost_summary
    }
    source_cost_valid = bool(
        type(source_ref) is str
        and source_ref
        and response.get("from") == origin
        and object_ref == source_ref
        and (
            (
                source_flags == {"discard_self"}
                and type(cost_summary["discard_self"]) is int
                and cost_summary["discard_self"] == 1
                and origin == "hand"
                and destination == "graveyard"
            )
            or (
                source_flags == {"exile_self"}
                and type(cost_summary["exile_self"]) is int
                and cost_summary["exile_self"] == 1
                and destination == "exile"
            )
            or (
                source_flags == {"sac_self"}
                and type(cost_summary["sac_self"]) is int
                and cost_summary["sac_self"] == 1
                and origin == "battlefield"
                and destination == "graveyard"
            )
        )
    )
    if source_cost_valid:
        return object_ref
    if source_flags or type(source_ref) is not str or not source_ref:
        return None

    choices = cost_summary.get("choose_cost")
    raw_refs = response.get("cost_objects") or response.get("cost_cards")
    if (
        not isinstance(choices, (list, tuple))
        or len(choices) != 1
        or not isinstance(choices[0], Mapping)
        or not isinstance(raw_refs, (list, tuple))
        or len(raw_refs) != 1
        or type(raw_refs[0]) is not str
        or raw_refs[0] != object_ref
    ):
        return None
    choice = choices[0]
    contract = FIXED_ZONE_CHANGE_COST_CONTRACTS.get(
        str(choice.get("k") or "")
    )
    legal_refs = choice.get("legal_refs")
    if (
        contract is None
        or contract[0] != origin
        or contract[1] != destination
        or choice.get("z") != origin
        or type(choice.get("n")) is not int
        or choice.get("n") != 1
        or not isinstance(choice.get("q"), Mapping)
        or not isinstance(legal_refs, (list, tuple))
        or object_ref not in legal_refs
    ):
        return None
    return object_ref


__all__ = ["activation_zone_change_cost_reference"]
