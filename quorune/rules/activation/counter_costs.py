from __future__ import annotations

"""Canonical exact source-counter payment for activated abilities."""

from typing import Any, Protocol
from dataclasses import replace

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


def plan_source_counter_removal_cost(
    host: SourceCounterActivationCostHost,
    source: Any,
    ability: ActivatedAbility,
):
    """Plan the same identity-pinned exact removal for offers and payment."""

    cost = ability.source_counter_removal_cost
    if cost is None:
        return None
    try:
        return plan_counter_removals(
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
    except CounterRemovalError as exc:
        raise SourceCounterActivationCostError(
            "The source no longer has enough counters"
        ) from exc


def preview_source_counter_removal_cost(host, source, ability):
    """Return detached post-payment counter facts without committing a cost."""
    plan = plan_source_counter_removal_cost(host, source, ability)
    counters = dict(source.counters)
    if plan is not None:
        for transition in plan.counter_plan.transitions:
            counters[transition.counter_name] = transition.after
    return replace(source, counters=counters)


def commit_source_counter_removal_cost(host, source, ability) -> None:
    """Pay the exact plan used by the read-only source-counter preview."""
    plan = plan_source_counter_removal_cost(host, source, ability)
    if plan is not None:
        try:
            commit_counter_removals(host, plan)
        except CounterRemovalError as exc:
            raise SourceCounterActivationCostError("The source no longer has enough counters") from exc


__all__ = [
    "SourceCounterActivationCostError",
    "SourceCounterActivationCostHost",
    "commit_source_counter_removal_cost",
    "plan_source_counter_removal_cost",
    "preview_source_counter_removal_cost",
]
