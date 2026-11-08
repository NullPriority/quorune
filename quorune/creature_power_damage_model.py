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
