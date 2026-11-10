from __future__ import annotations

"""Closed layer-7d instructions and their independently required capabilities."""

from collections.abc import Mapping
import re

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION
from ..target_cardinality import ANY_NUMBER_TARGET_CAPABILITY, validate_selected_target_count


SWITCH_CAPABILITY = "continuous.resolution.fixed_power_toughness_switch"
SWITCH_MECHANIC = "fixed-power-toughness-switch"
SWITCH_SCHEMA_VERSION = 12
SWITCH_CREATURE_SET = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
_BASE_CAPABILITY = "continuous.resolution.fixed_characteristics_until_end_of_turn"
_TARGET_CAPABILITY = "target.revalidate_resolution"
_SCALAR_TARGET = re.compile(r"\$target\.0|\$target_group\.(?:clause_[0-3]|target_[0-9]+)\.0")


def switch_target_schema() -> dict:
    return {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}


def is_power_toughness_switch(value) -> bool:
    return (isinstance(value, Mapping) and value.get("op") == CHARACTERISTIC_OPERATION
        and value.get("schema_version") == SWITCH_SCHEMA_VERSION and "characteristics" not in value)


def decode_switch_recipient(value, *, resolved: bool = False):
    base = {"op", "schema_version"}
    if not is_power_toughness_switch(value) or type(value["schema_version"]) is not int:
        raise ValueError("Power and toughness switch identity is malformed")
    if set(value) == base | {"card"} and type(value["card"]) is str and value["card"]:
        return value["card"]
    if set(value) == base | {"predicate"} and thaw_value(value["predicate"]) == SWITCH_CREATURE_SET.to_dict():
        return SWITCH_CREATURE_SET
    announced = value.get("announced_target_count") is True
    expected = base | {"cards", "maximum_targets"} | ({"announced_target_count"} if announced else set())
    if set(value) != expected:
        raise ValueError("Power and toughness switch selection fields are closed")
    maximum = value["maximum_targets"]
    if announced and not resolved:
        validate_selected_target_count(maximum)
    elif type(maximum) is not int or not (maximum >= 0 if announced else 1 <= maximum <= 6):
        raise ValueError("Power and toughness switch target limit is malformed")
    refs = value["cards"]
    if not resolved:
        if refs != "$targets":
            raise ValueError("Power and toughness switch requires its chosen target set")
        return refs
    if (not isinstance(refs, (tuple, list)) or any(type(ref) is not str or not ref for ref in refs)
        or len(set(refs)) != len(refs) or len(refs) > maximum):
        raise ValueError("Power and toughness switch target references are malformed")
    return tuple(refs)


def power_toughness_switch_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if not {SWITCH_MECHANIC, "cr-611-continuous-effects"} <= mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        selection = decode_switch_recipient(effect)
    except (ValueError, TypeError, KeyError):
        return ()
    required = (SWITCH_CAPABILITY, _BASE_CAPABILITY)
    if selection == "$source.zone_object" or selection == SWITCH_CREATURE_SET:
        return required if target_schema is None else ()
    expected = switch_target_schema()
    if selection == "$targets" and "announced_target_count" not in effect:
        del expected["count"]
        expected["up_to"] = effect["maximum_targets"]
    elif selection != "$target.0":
        return ()
    return (*required, _TARGET_CAPABILITY) if (
        "cr-115-targets" in mechanics and thaw_value(target_schema) == expected
    ) else ()


def power_toughness_switch_runtime_dependencies(effect, *, target_schema):
    """Require the actual effect's owners even when it sits in a composed body."""
    recipient = decode_switch_recipient(effect)
    required = {SWITCH_CAPABILITY, _BASE_CAPABILITY}
    if isinstance(recipient, str) and recipient != "$source.zone_object":
        if target_schema is None or not (recipient == "$targets" or _SCALAR_TARGET.fullmatch(recipient)):
            raise ValueError("Power and toughness switch requires an independently declared target")
        required.add(_TARGET_CAPABILITY)
    if effect.get("announced_target_count") is True:
        required.add(ANY_NUMBER_TARGET_CAPABILITY)
    return tuple(sorted(required))
