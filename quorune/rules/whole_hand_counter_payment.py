from __future__ import annotations

"""The optional whole-hand price of a single ordinary target spell counter."""

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from .stack_controller_payment_cost import STACK_CONTROLLER_PAYMENT_CAPABILITY


VERSION = 7
FIELD = "discard_whole_hand"
CAPABILITY = "stack.counter.controller_whole_hand_payment"
HAND_PAYMENT_SNAPSHOT = "_whole_hand_payment_snapshot"


def whole_hand_counter_schema():
    return {"zones": ["stack"], "categories": ["spell"], "source_exclusion": True, "count": 1}


def whole_hand_counter_effect(*, target="$target.0", actor="$target.current_controller.0"):
    return {"op": "counter_unless_pay", "schema_version": VERSION,
            "stack": target, "player": actor, FIELD: 1}


def marks_whole_hand_counter(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "counter_unless_pay"
            and (effect.get("schema_version") == VERSION or FIELD in effect))


def validate_whole_hand_counter_effect(effect, *, target="$target.0", actor="$target.current_controller.0"):
    if (not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int
            or type(effect.get(FIELD)) is not int
            or thaw_value(effect) != whole_hand_counter_effect(target=target, actor=actor)):
        raise ValueError("Whole-hand counter payment requires the exact controller and spell-target roles")


def whole_hand_counter_dependencies(effect, target_schema):
    validate_whole_hand_counter_effect(effect)
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or target_schema.get("source_exclusion") is not True
            or thaw_value(target_schema) != whole_hand_counter_schema()):
        raise ValueError("Whole-hand counter payment requires one ordinary spell target")
    return (
        CAPABILITY, STACK_CONTROLLER_PAYMENT_CAPABILITY, "stack.counter.effect",
        "target.revalidate_resolution", "zone.discard.whole_hand",
    )


def whole_hand_counter_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if marks_whole_hand_counter(effect))
    if not marked:
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack") or not program.ability_id.startswith("spell:")
            or tuple(program.effects) != marked or len(marked) != 1 or program.handlers
            or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Whole-hand counter payment requires its original resolving spell")
    return whole_hand_counter_dependencies(marked[0], program.target_schema)
