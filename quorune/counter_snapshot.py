from __future__ import annotations

"""Immutable normalized counter snapshots for last-known information."""

from typing import Any, Mapping

from .replacement.immutable import FrozenMap


ANY_COUNTER_QUERY_CAPABILITY = "state_query.permanent.any_counter"
COUNTER_TOTAL_CAPABILITY = "quantity_expression.public_counter_total"


class CounterSnapshotError(ValueError):
    """A permanent counter snapshot is malformed."""


def permanent_counter_snapshot(counters: Mapping[str, Any]) -> FrozenMap:
    """Deep-freeze one permanent's public counters for last-known information."""

    if not isinstance(counters, Mapping):
        raise CounterSnapshotError("Permanent counter state must be a mapping")
    normalized: dict[str, int] = {}
    seen: set[str] = set()
    for raw_name, raw_amount in counters.items():
        if type(raw_name) is not str or not raw_name.strip():
            raise CounterSnapshotError(
                "Permanent counter names must be nonempty strings"
            )
        if type(raw_amount) is not int or raw_amount < 0:
            raise CounterSnapshotError(
                "Permanent counter amounts must be nonnegative integers"
            )
        name = " ".join(raw_name.casefold().split())
        if name in seen:
            raise CounterSnapshotError(
                "Permanent counter names must remain unique after normalization"
            )
        seen.add(name)
        if raw_amount:
            normalized[name] = raw_amount
    return FrozenMap(dict(sorted(normalized.items())))


def permanent_counter_total(counters: Mapping[str, Any]) -> int:
    """Count individual counters of every kind from one validated public state."""
    return sum(permanent_counter_snapshot(counters).values())


__all__ = ["CounterSnapshotError", "permanent_counter_snapshot", "permanent_counter_total"]
