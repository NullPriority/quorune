from __future__ import annotations

"""Closed identities and immutable committed counter occurrence facts."""

from collections.abc import Mapping
from dataclasses import dataclass
from .replacement.immutable import FrozenMap

COUNTER_PLACEMENT_EVENT='counter.put'
SINGLE_COUNTER_EVENT='counter.single_put'
COUNTER_EVENT_CAPABILITY='trigger.event.normalized_counter_placement'
COUNTER_EVENT_MECHANIC='trigger-event-normalized-counter-placement'


def counter_placement_condition_names_kind(condition):
    if not isinstance(condition, Mapping):
        return False
    if set(condition) == {"field", "op", "value"}:
        return (condition["field"] == "counter" and condition["op"] == "eq"
                and type(condition["value"]) is str and bool(condition["value"]))
    clauses = condition.get("all")
    return isinstance(clauses, (list, tuple)) and any(map(counter_placement_condition_names_kind, clauses))


def counter_placement_event_dependencies(program, subscriptions):
    events = (program.event, *(child.event for child in subscriptions.subscriptions)) if subscriptions else (program.event,)
    normalized = {event.removesuffix(".self") for event in events}
    if not normalized.intersection({COUNTER_PLACEMENT_EVENT, SINGLE_COUNTER_EVENT}):
        return ()
    if normalized != {COUNTER_PLACEMENT_EVENT} or not counter_placement_condition_names_kind(program.event_condition):
        from .scalar_effect_amount_model import ScalarAmountOrigin, scalar_amount_specs

        if any(spec.origin is ScalarAmountOrigin.EVENT_AMOUNT for spec in scalar_amount_specs(program.effects)):
            raise ValueError("Counter amounts require a grouped placement event with a named counter kind")
    return (COUNTER_EVENT_CAPABILITY,)


def counter_quantity_provenance_version(value: int | None) -> int | None:
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError("Unsupported counter quantity provenance version")
    return value


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
