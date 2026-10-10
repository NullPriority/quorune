from __future__ import annotations

"""Closed identities and immutable committed counter occurrence facts."""

from dataclasses import dataclass
from .replacement.immutable import FrozenMap

COUNTER_PLACEMENT_EVENT='counter.put'
SINGLE_COUNTER_EVENT='counter.single_put'
COUNTER_EVENT_CAPABILITY='trigger.event.normalized_counter_placement'
COUNTER_EVENT_MECHANIC='trigger-event-normalized-counter-placement'


@dataclass(frozen=True,slots=True)
class CounterPlacementOccurrence:
    context: FrozenMap

    def __post_init__(self):
        object.__setattr__(self,'context',FrozenMap(self.context))
        value=self.context
        if type(value.get('amount')) is not int or value['amount']<=0 or type(value.get('counter')) is not str or not value['counter']:
            raise ValueError('Counter placement occurrence requires a positive actual quantity')
        if any(type(value.get(field)) is not str or not value[field] for field in ('card','card_object_identity','event_id','placing_player','controller','owner')):
            raise ValueError('Counter placement occurrence identity is unavailable')
