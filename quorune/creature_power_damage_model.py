from __future__ import annotations

"""Stable identifiers for the fixed creature-power damage contract."""

from collections.abc import Mapping
from typing import Any


CREATURE_POWER_DAMAGE_OPERATION = "creature_power_damage"
CREATURE_POWER_DAMAGE_CAPABILITY = "damage.creature_power.fixed_interaction"
CREATURE_POWER_DAMAGE_MECHANIC = "fixed-creature-power-damage"
CREATURE_POWER_DAMAGE_LKI_CONTEXT = "creature_power_damage_source_lki"
CREATURE_POWER_DAMAGE_MULTIPLE_CAPABILITY = 'damage.creature_power.fixed_multiple'
OCCURRENCE_POWER_DAMAGE_VERSION = 3
OCCURRENCE_POWER_DAMAGE_CONTEXT = "occurrence_power_damage_lki"
OCCURRENCE_POWER_DAMAGE_SOURCE = "$context.card"
OCCURRENCE_POWER_DAMAGE_LKI = f"$context.{OCCURRENCE_POWER_DAMAGE_CONTEXT}"
ORIGINAL_SOURCE_RECIPIENT_VERSION = 4
CREATURE_SELF_DAMAGE_SET_VERSION = 5
CREATURE_SELF_DAMAGE_SET_KIND = "self_set"
CREATURE_SELF_DAMAGE_SET_CAPABILITY = "damage.creature_power.simultaneous_self_set"
CREATURE_SELF_DAMAGE_SET_DEPENDENCIES = (
    CREATURE_SELF_DAMAGE_SET_CAPABILITY,
    "damage.amount.positive",
    "damage.result.multitype_permanent",
)


def creature_self_damage_set_effect() -> dict[str, Any]:
    return {
        "op": CREATURE_POWER_DAMAGE_OPERATION,
        "schema_version": CREATURE_SELF_DAMAGE_SET_VERSION,
        "kind": CREATURE_SELF_DAMAGE_SET_KIND,
    }


def is_creature_self_damage_set(effect: Mapping[str, Any]) -> bool:
    return effect.get("op") == CREATURE_POWER_DAMAGE_OPERATION and (
        effect.get("kind") == CREATURE_SELF_DAMAGE_SET_KIND
        or effect.get("schema_version") == CREATURE_SELF_DAMAGE_SET_VERSION
    )


def validate_creature_self_damage_set(effect: Mapping[str, Any], *, resolved: bool = False) -> None:
    expected = creature_self_damage_set_effect()
    actual = dict(effect)
    if resolved:
        for field in ("reason", "_runtime_source", "_replacement_event_ids", "_replacement_selections"):
            actual.pop(field, None)
    if type(actual.get("schema_version")) is not int or actual != expected:
        raise ValueError("Simultaneous creature self-damage requires the exact untargeted set")


def creature_power_damage_runtime_dependencies(effect: Mapping[str, Any]) -> tuple[str, ...]:
    if is_creature_self_damage_set(effect):
        validate_creature_self_damage_set(effect)
        return CREATURE_SELF_DAMAGE_SET_DEPENDENCIES
    if creature_power_damage_multiplier(effect) == 2:
        return (CREATURE_POWER_DAMAGE_MULTIPLE_CAPABILITY,)
    return ()


def occurrence_power_damage_in(value):
    """Find versioned occurrence leaves, including a paid continuation."""
    if isinstance(value, Mapping):
        if value.get("op") == CREATURE_POWER_DAMAGE_OPERATION and value.get("schema_version") == OCCURRENCE_POWER_DAMAGE_VERSION:
            yield value
        for child in value.values():
            yield from occurrence_power_damage_in(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from occurrence_power_damage_in(child)


def creature_power_damage_multiplier(value: Mapping[str, Any]) -> int:
    """Version two owns fixed doubling."""
    fields = {'schema_version', 'power_multiplier'} & set(value)
    if not fields:
        return 1
    if (fields == {'schema_version'} and type(value['schema_version']) is int
            and value['schema_version'] == OCCURRENCE_POWER_DAMAGE_VERSION and value.get('kind') == 'bite'):
        return 1
    if (fields == {'schema_version'} and type(value['schema_version']) is int
            and value['schema_version'] == ORIGINAL_SOURCE_RECIPIENT_VERSION
            and value.get('kind') == 'bite' and value.get('recipient_is_original_source') is True):
        return 1
    if (fields != {'schema_version', 'power_multiplier'}
            or type(value['schema_version']) is not int or value['schema_version'] != 2
            or type(value['power_multiplier']) is not int or value['power_multiplier'] != 2
            or value.get('kind') != 'bite'):
        raise ValueError('Creature-power multiplier is malformed')
    return 2


__all__ = [
    "CREATURE_POWER_DAMAGE_CAPABILITY",
    "CREATURE_POWER_DAMAGE_LKI_CONTEXT",
    "CREATURE_POWER_DAMAGE_MECHANIC",
    "CREATURE_POWER_DAMAGE_OPERATION",
    "CREATURE_POWER_DAMAGE_MULTIPLE_CAPABILITY",
    "creature_power_damage_multiplier",
]
