"""Strict target and occurrence roles for copying a departing permanent's counters."""

from collections.abc import Mapping

from ..counter_snapshot import permanent_counter_snapshot
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


DEPARTURE_COUNTER_TRANSFER_CAPABILITY = "counter.producer.departure_snapshot_transfer"
DEPARTURE_COUNTER_TRANSFER_OPERATION = "place_departure_counters"
DEPARTURE_COUNTER_TRANSFER_COVERAGE = "departure_counter_transfer"
DEPARTURE_COUNTER_SNAPSHOT_FIELD = "departure_counter_snapshot"
DEPARTURE_COUNTER_EVENT = "permanent.leave"
DEPARTURE_COUNTER_MECHANICS = (DEPARTURE_COUNTER_TRANSFER_COVERAGE, "cr-122-counters", "cr-115-targets")
DEPARTURE_COUNTER_DEPENDENCIES = (
    DEPARTURE_COUNTER_TRANSFER_CAPABILITY, "counter.placement.quantity_replacement",
    "target.revalidate_resolution", "trigger.effect.fixed_event",
    "trigger.event.normalized_zone_change", "trigger.placement.apnap",
)


def departure_counter_condition():
    return {"all": [
        {"field": "previous_controller", "op": "eq", "value": "$source.controller"},
        {"field": DEPARTURE_COUNTER_SNAPSHOT_FIELD, "op": "truthy"},
    ]}


def departure_counter_target_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "controller_relation": "you"}


def departure_counter_effect():
    return {
        "op": DEPARTURE_COUNTER_TRANSFER_OPERATION, "player": "$controller", "card": "$target.0",
        "counters": f"$context.{DEPARTURE_COUNTER_SNAPSHOT_FIELD}",
    }


def departure_counter_snapshot(value: Mapping[str, int]) -> FrozenMap:
    snapshot = permanent_counter_snapshot(value)
    if snapshot != value:
        raise ValueError("A departure transfer requires exact positive canonical counter amounts")
    return snapshot


def departure_counter_transfer_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if (cost_schema is None and isinstance(target_schema, Mapping)
            and len(effects) == 1 and FrozenMap(effects[0]) == FrozenMap(departure_counter_effect())
            and FrozenMap(target_schema) == FrozenMap(departure_counter_target_schema())
            and set(DEPARTURE_COUNTER_MECHANICS) <= set(mechanic_ids)):
        return DEPARTURE_COUNTER_DEPENDENCIES
    return ()


def departure_counter_transfer_program_dependencies(program):
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                   if effect.get("op") == DEPARTURE_COUNTER_TRANSFER_OPERATION)
    if (not marked and DEPARTURE_COUNTER_TRANSFER_CAPABILITY not in program.capability_dependencies
            and DEPARTURE_COUNTER_TRANSFER_COVERAGE not in program.coverage):
        return ()
    expected = departure_counter_transfer_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        cost_schema=program.cost_schema,
    )
    if (not expected or effects != marked or (program.event, program.active_zone) != (DEPARTURE_COUNTER_EVENT, "battlefield")
            or program.handlers or "intervening_condition" not in program.coverage
            or FrozenMap(program.event_condition or {}) != FrozenMap(departure_counter_condition())):
        raise ValueError("A departure transfer must keep its original controlled departure and exact targeted result")
    return expected


def is_closed_departure_counter_transfer_program(program):
    try:
        required = departure_counter_transfer_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def departure_counter_condition_holds(context, controller):
    return (context.get("event") == DEPARTURE_COUNTER_EVENT
            and controller is not None and context.get("previous_controller") == controller
            and bool(departure_counter_snapshot(context.get(DEPARTURE_COUNTER_SNAPSHOT_FIELD))))
