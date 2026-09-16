from __future__ import annotations

"""Canonical exact source-counter payment for activated abilities."""

from typing import Any, Protocol

from ...abilities import ActivatedAbility
from ...counter_removal import (
    commit_counter_removals,
    CounterRemoval,
    CounterRemovalError,
    plan_counter_removals,
)


class SourceCounterActivationCostError(ValueError):
    """A typed source-counter activation cost cannot be paid exactly."""


class SourceCounterActivationCostHost(Protocol):
    state: Any


def commit_source_counter_removal_cost(
    host: SourceCounterActivationCostHost,
    source: Any,
    ability: ActivatedAbility,
) -> None:
    """Pay one identity-pinned exact source-counter removal, if present."""

    cost = ability.source_counter_removal_cost
    if cost is None:
        return
    try:
        plan = plan_counter_removals(
            host,
            (
                CounterRemoval(
                    object_id=source.object_id,
                    counter_name=cost.counter_name,
                    amount=cost.amount,
                    expected_zone="battlefield",
                    expected_logical_object_id=source.logical_object_id,
                ),
            ),
        )
        commit_counter_removals(host, plan)
    except CounterRemovalError as exc:
        raise SourceCounterActivationCostError(
            "The source no longer has enough counters"
        ) from exc


__all__ = [
    "SourceCounterActivationCostError",
    "SourceCounterActivationCostHost",
    "commit_source_counter_removal_cost",
]
