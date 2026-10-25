from __future__ import annotations

"""Read-only permission preview for represented optional counter instructions."""

from ..affected_permanents import select_affected_permanents
from ..semantic_runtime.intents import (
    PlaceCounterBatchIntent, PlaceCountersIntent, PlaceCountersOnSetIntent,
    PlaceCountersOnTargetsIntent, PlacePlayerCountersIntent,
)
from .model import SemanticChoiceError


def optional_counter_placement_possible(plan, query):
    for intent in plan.intents:
        if isinstance(intent, PlacePlayerCountersIntent):
            if any(query.counter_placement_allowed(seat, intent.counter_name, intent.amount,
                    subject_kind='player') for seat in intent.player_ids):
                return True
            continue
        if isinstance(intent, PlaceCounterBatchIntent):
            placements = ((intent.object_ref, item.counter_name, item.amount) for item in intent.placements)
        elif isinstance(intent, (PlaceCountersIntent, PlaceCountersOnTargetsIntent)):
            placements = ((ref, intent.counter_name, intent.amount) for ref in intent.object_refs)
        elif isinstance(intent, PlaceCountersOnSetIntent):
            rows = select_affected_permanents(query.objects(zones=('battlefield',)), intent.spec,
                actor=intent.actor, active_seats=query.active_seats,
                apnap_order=query.active_seats, source_ref=intent.source_ref)
            placements = ((row.ref, intent.counter_name, intent.amount) for row in rows)
        else:
            raise SemanticChoiceError('Optional placement preview requires a represented counter intent')
        for ref, counter, amount in placements:
            row = query.object(ref, zones=('battlefield',))
            if row is None or row.phased_out:
                continue
            names = tuple(row.counters) if amount == 'existing_all_counter_counts' else (counter,)
            if amount == 'existing_all_counter_counts' and not names:
                return True
            for name in names:
                quantity = row.counters.get(name, 0) if isinstance(amount, str) else amount
                if query.counter_placement_allowed(ref, name, quantity):
                    return True
    return False
