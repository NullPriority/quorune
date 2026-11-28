"""Closed Gift a card counter with declaration-dependent spell targeting."""

from collections.abc import Mapping

from ..gift_card_model import GIFT_CARD_CAPABILITY
from ..replacement.immutable import thaw_value

GIFT_COUNTER_COST_FIELD = "gift_a_card_counter_target"
GIFT_COUNTER_TEMPLATE = "gift-card-counter-target-replacement-v1"
GIFT_COUNTER_CAPABILITY = "casting.additional_cost.gift_card_counter_target_replacement"
GIFT_COUNTER_DEPENDENCIES = (
    GIFT_COUNTER_CAPABILITY, GIFT_CARD_CAPABILITY, "stack.counter.effect",
    "target.revalidate_resolution", "zone.draw.library_to_hand",
)


def gift_counter_effect():
    return {"op": "counter_stack_target", "stack": "$target.0"}


def gift_counter_target_schema(promised):
    base = {"zones": ["stack"], "categories": ["spell"], "source_exclusion": True, "count": 1}
    return base if promised else {**base, "types_any": ["creature"]}


def gift_counter_cost_schema():
    return {GIFT_COUNTER_COST_FIELD: {
        "schema_version": 1, "unpromised": gift_counter_target_schema(False),
        "promised": gift_counter_target_schema(True),
    }}


def gift_counter_runtime_dependencies(program):
    if program is None:
        return ()
    cost = program.cost_schema
    if not (isinstance(cost, Mapping) and GIFT_COUNTER_COST_FIELD in cost
            or (program.provenance or {}).get("template_id") == GIFT_COUNTER_TEMPLATE):
        return ()
    price = cost.get(GIFT_COUNTER_COST_FIELD) if isinstance(cost, Mapping) else None
    if (thaw_value(cost) != gift_counter_cost_schema() or not isinstance(price, Mapping)
            or type(price["schema_version"]) is not int
            or any(type(price[branch]["count"]) is not int or price[branch]["source_exclusion"] is not True
                   for branch in ("unpromised", "promised"))
            or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers
            or thaw_value(program.target_schema) != gift_counter_target_schema(False)
            or type(program.target_schema["count"]) is not int or program.target_schema["source_exclusion"] is not True
            or tuple(thaw_value(program.effects)) != (gift_counter_effect(),)):
        raise ValueError("Gift a card counter requires the declared creature-only or any-spell target and one counter")
    return GIFT_COUNTER_DEPENDENCIES


def is_closed_gift_counter_spell(program):
    try:
        required = gift_counter_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
