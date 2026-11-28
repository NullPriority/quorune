"""The announced legal target set for three ordered temporary control results."""

from __future__ import annotations

from collections.abc import Mapping

from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..target_cardinality import (
    ANY_NUMBER_TARGET_CAPABILITY, AnnouncedTargetCardinality, selected_target_count_descriptor,
    validate_selected_target_count,
)
from .effect_shape_walkers import contains_nested_key


TARGETED_CONTROL_SET_VERSION = "targeted_control_set_version"
TARGETED_CONTROL_SET_CAPABILITY = "continuous.control.announced_creature_set"
TARGETED_CONTROL_SET_STEPS = ("gain_control", "untap", "haste")
TARGETED_CONTROL_SET_MECHANICS = (
    "any-number-target-set", "cr-115-targets", "fixed-resolution-control",
    "cr-611-continuous-effects", "tap-and-untap", "haste",
)
TARGETED_CONTROL_SET_RECEIVERS = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


def targeted_control_set_schema(*, per_opponent=False):
    schema = {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
        "count_parameter": AnnouncedTargetCardinality(
            selection="up_to", parameter="any_number", schema_version=2,
        ).to_dict(),
    }
    if per_opponent:
        schema.update(controller_relation="opponent", distinct_controllers=True, retain_controller=True)
    return schema


def targeted_control_set_effect():
    return {
        "op": "gain_control_set", TARGETED_CONTROL_SET_VERSION: 1,
        "cards": "$targets", "maximum_targets": selected_target_count_descriptor(),
        "announced_target_count": True, "controller": "$controller", "source": "$source",
        "duration": "until_end_of_turn", "steps": list(TARGETED_CONTROL_SET_STEPS),
    }


def validate_targeted_control_set(effect, *, resolved=False):
    expected = targeted_control_set_effect()
    if (not isinstance(effect, Mapping) or type(effect.get(TARGETED_CONTROL_SET_VERSION)) is not int
            or effect[TARGETED_CONTROL_SET_VERSION] != 1):
        raise ValueError("Announced creature control requires its exact descriptor version")
    if not resolved:
        if thaw_value(effect) != expected or effect.get("announced_target_count") is not True:
            raise ValueError("Announced creature control requires the exact printed set and order")
        validate_selected_target_count(effect["maximum_targets"])
        return ()
    variable = {"cards", "maximum_targets", "controller", "source"}
    if (set(effect) - {"reason", "_runtime_source"} != set(expected)
            or effect.get("announced_target_count") is not True
            or any(thaw_value(effect[key]) != value for key, value in expected.items() if key not in variable)
            or any(type(effect[key]) is not str or not effect[key] or effect[key].startswith("$")
                   for key in ("controller", "source"))):
        raise ValueError("Resolved creature control requires its original actor, source and ordered result")
    cards = effect["cards"]
    maximum = effect["maximum_targets"]
    if (not isinstance(cards, (tuple, list))
            or any(type(ref) is not str or not ref or ref.startswith("$") for ref in cards)
            or len(set(cards)) != len(cards)
            or type(maximum) is not int or maximum < len(cards)):
        raise ValueError("Creature control needs distinct legal targets within the original target count")
    return tuple(cards)


def targeted_control_set_node_capabilities(*, effects, target_schema):
    schema = thaw_value(target_schema)
    per_opponent = schema == targeted_control_set_schema(per_opponent=True)
    if per_opponent and (schema["distinct_controllers"] is not True or schema["retain_controller"] is not True):
        return ()
    if (len(effects) != 1 or not (per_opponent or schema == targeted_control_set_schema())):
        return ()
    try:
        validate_targeted_control_set(effects[0])
    except (TypeError, ValueError):
        return ()
    return tuple(sorted({
        TARGETED_CONTROL_SET_CAPABILITY, ANY_NUMBER_TARGET_CAPABILITY, "target.revalidate_resolution",
        "continuous.control.fixed_resolution", "continuous.resolution.fixed_characteristics_until_end_of_turn",
        "permanent.untap.effect", *FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["Haste"],
        *(("target.set.one_per_player",) if per_opponent else ()),
    }))


def targeted_control_set_runtime_dependencies(program):
    if not contains_nested_key((program.effects, program.handlers, program.target_schema), TARGETED_CONTROL_SET_VERSION):
        return ()
    required = targeted_control_set_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.event_condition is not None or program.handlers):
        raise ValueError("Announced creature control requires its whole ordinary resolving spell")
    return required
