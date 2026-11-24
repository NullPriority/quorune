from __future__ import annotations

"""A self-copy that retains the original targeted upkeep trigger."""

from collections.abc import Mapping

from ..replacement.face_status_copy import upkeep_refacing_condition
from ..replacement.immutable import thaw_value


PERSISTENT_SELF_COPY_TRIGGER_FIELD = "persistent_self_copy_trigger_version"
PERSISTENT_SELF_COPY_TRIGGER_CAPABILITY = "continuous.resolution.self_copy_retains_trigger"
PERSISTENT_SELF_COPY_TRIGGER_TEMPLATE = "self-copy-retains-upkeep-trigger-v1"
PERSISTENT_SELF_COPY_TRIGGER_LEAF = (
    PERSISTENT_SELF_COPY_TRIGGER_CAPABILITY, "target.revalidate_resolution",
)
PERSISTENT_SELF_COPY_TRIGGER_DEPENDENCIES = (
    *PERSISTENT_SELF_COPY_TRIGGER_LEAF, "effect.choice.optional_fixed", "trigger.placement.apnap",
)


def self_copy_upkeep_target():
    return {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
            "count": 1, "another": True}


def self_copy_upkeep_instruction():
    return {"op": "copy_until_end_of_turn", PERSISTENT_SELF_COPY_TRIGGER_FIELD: 1,
            "source": "$source.zone_object", "target": "$target.0"}


def self_copy_upkeep_effect():
    return {"op": "offer_optional_effect", "player": "$controller",
            "effects": [self_copy_upkeep_instruction()]}


def is_persistent_self_copy_trigger(effect):
    return isinstance(effect, Mapping) and PERSISTENT_SELF_COPY_TRIGGER_FIELD in effect


def validate_self_copy_upkeep_instruction(effect, *, resolved=False):
    expected = self_copy_upkeep_instruction()
    if (not is_persistent_self_copy_trigger(effect)
            or type(effect[PERSISTENT_SELF_COPY_TRIGGER_FIELD]) is not int
            or effect[PERSISTENT_SELF_COPY_TRIGGER_FIELD] != 1):
        raise ValueError("Self-copy with a retained trigger requires its version-one instruction")
    if not resolved:
        if dict(effect) != expected:
            raise ValueError("Self-copy requires its original source and announced target")
    elif (
        set(effect) - {"_runtime_source", "reason"} != set(expected) or effect["op"] != expected["op"]
        or type(effect["target"]) is not str or not effect["target"]
        or (effect["source"] is not None and (type(effect["source"]) is not str or not effect["source"]))
    ):
        raise ValueError("Self-copy requires its resolved source and announced target")


def persistent_self_copy_trigger_dependencies(program):
    if (
        program.event != "step.begin" or program.active_zone != "battlefield"
        or getattr(program, "cost_schema", None) is not None
        or thaw_value(program.event_condition) != upkeep_refacing_condition()
        or thaw_value(program.target_schema) != self_copy_upkeep_target()
        or thaw_value(program.effects) != [self_copy_upkeep_effect()]
        or "intervening_condition" in program.coverage
    ):
        raise ValueError("Self-copy requires the exact original upkeep trigger and its optional result")
    validate_self_copy_upkeep_instruction(program.effects[0]["effects"][0])
    return PERSISTENT_SELF_COPY_TRIGGER_DEPENDENCIES


def is_closed_persistent_self_copy_trigger(program):
    try:
        required = persistent_self_copy_trigger_dependencies(program)
    except (KeyError, TypeError, ValueError):
        return False
    return set(required).issubset(program.capability_dependencies)
