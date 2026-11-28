from __future__ import annotations

"""Coordinate CR 122.5 through the existing counter state and placement owners."""

from dataclasses import replace
from ..characteristic_evaluation import type_parts
from ..fixed_counter_movement_model import SOURCE_GIVES_COUNTERS_THEN_DRAW, SOURCE_RECEIVES_MISSING_KINDS
from ..counter_placement import (
    CounterPlacementRequest, finish_counter_placement_commit,
    plan_prepared_counter_placement_commit, prepare_counter_placements,
)
from ..counter_snapshot import permanent_counter_snapshot
from ..counter_state import CounterChange, commit_counter_changes, plan_counter_changes
from ..semantic_runtime.counter_movement_intent import (
    CounterMovementIntent, DistributeSourceCountersIntent, MoveCounterSetIntent,
    counter_set_controllers_match,
)
from .committed_quantity_followups import CommittedQuantityFollowup, fixed_quantity_effect


COUNTER_MOVEMENT_DRAW = fixed_quantity_effect({
    "op": "draw", "player": "$controller", "count": 1, "private": True,
})


def _live_object(by_ref, ref, identity):
    card = by_ref.get(ref)
    if (card is None or card.zone != "battlefield" or card.phased_out
            or card.logical_object_id != identity):
        return None
    return card


def _distribution_requests(intent, by_ref, source):
    if source.counters.get(intent.counter_name, 0) < intent.amount:
        return ()
    requests = []
    for share in intent.allocations:
        recipient = _live_object(by_ref, share.ref, share.identity)
        if recipient is None or recipient is source:
            return ()
        requests.append(CounterPlacementRequest(
            subject_kind="permanent", subject_id=recipient.object_id,
            counter_name=intent.counter_name, amount=share.amount,
            placing_player=intent.actor, source_ref=intent.source_ref,
        ))
    return tuple(requests)


def _single_recipient_requests(host, intent, by_ref, source):
    recipient = _live_object(by_ref, intent.to_ref, intent.to_identity)
    if (recipient is None or recipient is source
            or any(source.counters.get(kind, 0) < amount for kind, amount in intent.amounts.items())):
        return ()
    if isinstance(intent, MoveCounterSetIntent):
        if intent.all_to_creature:
            types, _, _ = type_parts(host._effective_card_data(recipient)["type_line"])
            if "creature" not in types or permanent_counter_snapshot(source.counters) != intent.amounts:
                return ()
        elif not counter_set_controllers_match(intent.mode, intent.actor, source.controller, recipient.controller):
            return ()
    if (isinstance(intent, MoveCounterSetIntent) and intent.mode == SOURCE_RECEIVES_MISSING_KINDS
            and any(recipient.counters.get(kind, 0) > 0 for kind in intent.amounts)):
        return ()
    return tuple(
        CounterPlacementRequest(
            subject_kind="permanent", subject_id=recipient.object_id,
            counter_name=kind, amount=amount, placing_player=intent.actor, source_ref=intent.source_ref,
        )
        for kind, amount in intent.amounts.items()
    )


def execute_counter_movement(host, intent: CounterMovementIntent) -> tuple[str, ...] | CommittedQuantityFollowup:
    """Resolve placement choices before atomically removing and placing counters."""
    by_ref = {card.ref: card for card in host.state.cards.values()}
    source = _live_object(by_ref, intent.from_ref, intent.from_identity)
    if source is None:
        return ()
    distributing = isinstance(intent, DistributeSourceCountersIntent)
    requests = (_distribution_requests(intent, by_ref, source) if distributing
                else _single_recipient_requests(host, intent, by_ref, source))
    if not requests:
        return ()
    prepared = prepare_counter_placements(host, requests, selections=intent.replacement_selections)
    placement = plan_prepared_counter_placement_commit(host, prepared)
    gives_counters_then_draw = isinstance(intent, MoveCounterSetIntent) and intent.mode == SOURCE_GIVES_COUNTERS_THEN_DRAW
    successful = tuple(row for row in placement.rows if row.amount > 0)
    if not successful:
        # CR 118.11–12: a replacement may change a possible cost after payment began.
        return CommittedQuantityFollowup((COUNTER_MOVEMENT_DRAW,)) if gives_counters_then_draw else ()
    removal_amounts = (
        ((intent.counter_name, sum(row.requested for row in successful)),)
        if distributing
        else tuple((row.counter_name, intent.amounts[row.counter_name]) for row in successful)
    )
    removals = tuple(
        CounterChange(
            subject_kind="permanent", subject_id=source.object_id, counter_name=kind, amount=-amount,
            expected_zone="battlefield", expected_logical_object_id=intent.from_identity,
        )
        for kind, amount in removal_amounts
    )
    removal_plan = plan_counter_changes(host, removals)
    joint = replace(removal_plan, transitions=(
        *removal_plan.transitions, *placement.state_plan.transitions))
    # Commit all state before dispatching either removal or placement observations.
    commit_counter_changes(host, joint)
    for removal in removal_plan.transitions:
        amount = -removal.applied_delta
        host._log(intent.actor, "permanent.counter",
                  f"Moved {amount} {removal.counter_name} counter(s) from {source.ref}.",
                  {"object": source.ref, "counter": removal.counter_name,
                   "delta": removal.applied_delta, "before": removal.before,
                   "after": removal.after, "source": intent.source_ref},
                  importance=1, changed_objects=[source.object_id])
    finish_counter_placement_commit(
        host, placement, placement.state_plan.transitions, reason=intent.reason)
    if gives_counters_then_draw:
        return CommittedQuantityFollowup((COUNTER_MOVEMENT_DRAW,))
    if distributing:
        return tuple(row.display_ref for row in successful)
    return (intent.to_ref,)
