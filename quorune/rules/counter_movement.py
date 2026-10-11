from __future__ import annotations

"""Coordinate CR 122.5 through the existing counter state and placement owners."""

from dataclasses import replace
from ..counter_placement import (
    CounterPlacementRequest, finish_counter_placement_commit,
    plan_prepared_counter_placement_commit, prepare_counter_placements,
)
from ..counter_state import CounterChange, commit_counter_changes, plan_counter_changes
from ..semantic_runtime.counter_movement_intent import MoveCountersIntent


def execute_counter_movement(host, intent: MoveCountersIntent) -> tuple[str, ...]:
    """Resolve placement choices before atomically removing and placing counters."""
    by_ref = {card.ref: card for card in host.state.cards.values()}
    source = by_ref.get(intent.from_ref)
    recipient = by_ref.get(intent.to_ref)
    if (source is None or recipient is None or source is recipient
            or source.zone != "battlefield" or recipient.zone != "battlefield"
            or source.phased_out or recipient.phased_out
            or source.logical_object_id != intent.from_identity
            or recipient.logical_object_id != intent.to_identity
            or source.counters.get(intent.counter_name, 0) < intent.amount):
        return ()
    prepared = prepare_counter_placements(host, (
        CounterPlacementRequest(
            subject_kind="permanent", subject_id=recipient.object_id,
            counter_name=intent.counter_name, amount=intent.amount,
            placing_player=intent.actor, source_ref=intent.source_ref),
    ), selections=intent.replacement_selections)
    placement = plan_prepared_counter_placement_commit(host, prepared)
    if not placement.rows or all(row.amount == 0 for row in placement.rows):
        return ()
    removal = CounterChange(
        subject_kind="permanent", subject_id=source.object_id,
        counter_name=intent.counter_name, amount=-intent.amount,
        expected_zone="battlefield", expected_logical_object_id=intent.from_identity)
    removal_plan = plan_counter_changes(host, (removal,))
    joint = replace(removal_plan, transitions=(
        *removal_plan.transitions, *placement.state_plan.transitions))
    # Commit all state before dispatching either removal or placement observations.
    commit_counter_changes(host, joint)
    host._log(intent.actor, "permanent.counter",
              f"Moved {intent.amount} {intent.counter_name} counter(s) from {source.ref}.",
              {"object": source.ref, "counter": intent.counter_name,
               "delta": -intent.amount, "before": removal_plan.transitions[0].before,
               "after": removal_plan.transitions[0].after, "source": intent.source_ref},
              importance=1, changed_objects=[source.object_id])
    finish_counter_placement_commit(
        host, placement, placement.state_plan.transitions, reason=intent.reason)
    return (recipient.ref,)
