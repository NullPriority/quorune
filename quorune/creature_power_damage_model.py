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


CREATURE_CONTROLLER_DAMAGE_SET_VERSION = 6
CREATURE_CONTROLLER_DAMAGE_SET_KIND = "controller_set"
CREATURE_CONTROLLER_DAMAGE_SET_CAPABILITY = "damage.creature_controller.simultaneous_fixed_set"
CREATURE_CONTROLLER_DAMAGE_SET_DEPENDENCIES = (
    CREATURE_CONTROLLER_DAMAGE_SET_CAPABILITY,
    "damage.amount.positive",
    "damage.result.player_life",
)


def creature_controller_damage_set_effect() -> dict[str, Any]:
    return {"op": CREATURE_POWER_DAMAGE_OPERATION, "schema_version": CREATURE_CONTROLLER_DAMAGE_SET_VERSION,
            "kind": CREATURE_CONTROLLER_DAMAGE_SET_KIND, "amount": 1}


def is_creature_controller_damage_set(effect: Mapping[str, Any]) -> bool:
    return effect.get("op") == CREATURE_POWER_DAMAGE_OPERATION and (
        effect.get("kind") == CREATURE_CONTROLLER_DAMAGE_SET_KIND
        or effect.get("schema_version") == CREATURE_CONTROLLER_DAMAGE_SET_VERSION
    )


def validate_creature_controller_damage_set(effect: Mapping[str, Any], *, resolved: bool = False) -> None:
    actual = dict(effect)
    if resolved:
        for field in ("reason", "_runtime_source", "_replacement_event_ids", "_replacement_selections"):
            actual.pop(field, None)
    if (type(actual.get("schema_version")) is not int or type(actual.get("amount")) is not int
            or actual != creature_controller_damage_set_effect()):
        raise ValueError("Creature-controller damage requires the exact untargeted fixed-one set")


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


SOURCE_POWER_OPPONENT_DAMAGE_VERSION = 7
SOURCE_POWER_OPPONENT_DAMAGE_KIND = "source_power_opponents"
SOURCE_POWER_OPPONENT_DAMAGE_CAPABILITY = "damage.creature_power.source_opponents"


def source_power_opponent_damage_effect():
    return {"op": CREATURE_POWER_DAMAGE_OPERATION, "schema_version": SOURCE_POWER_OPPONENT_DAMAGE_VERSION,
            "kind": SOURCE_POWER_OPPONENT_DAMAGE_KIND, "source": "$source",
            "source_lki": "$context." + CREATURE_POWER_DAMAGE_LKI_CONTEXT}


def uses_source_power_opponent_damage(effect):
    return effect.get("op") == CREATURE_POWER_DAMAGE_OPERATION and (
        effect.get("kind") == SOURCE_POWER_OPPONENT_DAMAGE_KIND
        or effect.get("schema_version") == SOURCE_POWER_OPPONENT_DAMAGE_VERSION
    )


def validate_source_power_opponent_damage(effect, *, resolved=False):
    expected = source_power_opponent_damage_effect()
    values = dict(effect)
    if resolved:
        for field in ("reason", "_runtime_source", "_replacement_event_ids", "_replacement_selections"):
            values.pop(field, None)
        ref = values.get("source")
        if (type(ref) is not str or not ref or ref.startswith("$")
                or not isinstance(values.get("source_lki"), Mapping)):
            raise ValueError("Source-power opponent damage requires its original public source")
        expected["source"] = ref
        expected["source_lki"] = values["source_lki"]
    if type(values.get("schema_version")) is not int or values != expected:
        raise ValueError("Source-power opponent damage requires its closed original-source result")

TARGET_POWER_DAMAGE_COHORT_VERSION = 8
TARGET_POWER_DAMAGE_COHORT_KIND = "target_power_creatures_and_opponents"
TARGET_POWER_DAMAGE_COHORT_CAPABILITY = "damage.creature_power.targeted_creatures_and_opponents"
TARGET_POWER_DAMAGE_COHORT_DEPENDENCIES = (
    TARGET_POWER_DAMAGE_COHORT_CAPABILITY,
    CREATURE_POWER_DAMAGE_CAPABILITY,
    "damage.amount.positive",
    "damage.batch.fixed_set",
    "damage.result.multitype_permanent",
    "damage.result.player_life",
    "target.revalidate_resolution",
)


def target_power_damage_cohort_effect(source="$target.0"):
    return {
        "op": CREATURE_POWER_DAMAGE_OPERATION,
        "schema_version": TARGET_POWER_DAMAGE_COHORT_VERSION,
        "kind": TARGET_POWER_DAMAGE_COHORT_KIND,
        "source": source,
    }


def target_power_damage_cohort_schema():
    return {"groups": [{
        "id": "fighter", "zones": ["battlefield"], "categories": ["permanent"],
        "types_any": ["creature"], "controller_relation": "you", "count": 1,
    }]}


def uses_target_power_damage_cohort(effect):
    kind = effect.get("kind")
    return effect.get("op") == CREATURE_POWER_DAMAGE_OPERATION and (
        kind == TARGET_POWER_DAMAGE_COHORT_KIND
        or (effect.get("schema_version") == TARGET_POWER_DAMAGE_COHORT_VERSION and kind != TRAMPLE_BITE_KIND)
    )


def validate_target_power_damage_cohort(effect, *, resolved=False):
    actual = dict(effect)
    source = "$target.0"
    if resolved:
        for field in ("reason", "_runtime_source", "_replacement_event_ids", "_replacement_selections"):
            actual.pop(field, None)
        source = actual.get("source")
        if type(source) is not str or not source or source.startswith("$"):
            raise ValueError("Target-power damage requires its current creature source")
    if type(actual.get("schema_version")) is not int or actual != target_power_damage_cohort_effect(source):
        raise ValueError("Target-power damage requires the closed creature and opponent cohort")


TRAMPLE_BITE_VERSION = 8
TRAMPLE_BITE_KIND = "target_creature_power_trample_excess"
TRAMPLE_BITE_CAPABILITY = "damage.creature_power.trample_excess"
TRAMPLE_BITE_DEPENDENCIES = (
    TRAMPLE_BITE_CAPABILITY, CREATURE_POWER_DAMAGE_CAPABILITY,
    "damage.amount.positive", "damage.result.multitype_permanent",
    "damage.result.player_life", "damage.result.deathtouch", "target.revalidate_resolution",
)


def trample_bite_effect():
    return {"op": CREATURE_POWER_DAMAGE_OPERATION, "schema_version": TRAMPLE_BITE_VERSION,
            "kind": TRAMPLE_BITE_KIND, "source": "$target.0", "target": "$target.1"}


def uses_trample_bite(effect):
    kind = effect.get("kind")
    return effect.get("op") == CREATURE_POWER_DAMAGE_OPERATION and (
        kind == TRAMPLE_BITE_KIND
        or (effect.get("schema_version") == TRAMPLE_BITE_VERSION and kind != TARGET_POWER_DAMAGE_COHORT_KIND)
    )


def validate_trample_bite(effect, *, resolved=False):
    actual = dict(effect)
    expected = trample_bite_effect()
    if resolved:
        for field in ("reason", "_runtime_source", "_replacement_event_ids", "_replacement_selections"):
            actual.pop(field, None)
        for field in ("source", "target"):
            ref = actual.get(field)
            if ref is not None and (type(ref) is not str or not ref or ref != ref.strip() or ref.startswith("$")):
                raise ValueError("Creature-power excess damage requires its original resolved target references")
            expected[field] = ref
    if type(actual.get("schema_version")) is not int or actual != expected:
        raise ValueError("Creature-power excess damage requires the exact two-target instruction")


def split_trample_bite_damage(amount, *, toughness, marked, deathtouch, loyalty=None, defense=None):
    """Return the permanent amount and CR 120.4a excess before prevention or replacement."""
    if (type(amount) is not int or amount < 0 or type(toughness) is not int
            or type(marked) is not int or marked < 0 or type(deathtouch) is not bool
            or any(value is not None and (type(value) is not int or value < 0) for value in (loyalty, defense))):
        raise ValueError("Creature-power excess damage requires valid current permanent facts")
    lethal = max(0, toughness - marked)
    if deathtouch:
        lethal = min(lethal, 1)
    retained = min(amount, lethal, *(value for value in (loyalty, defense) if value is not None))
    return retained, amount - retained


def creature_power_damage_runtime_dependencies(effect: Mapping[str, Any]) -> tuple[str, ...]:
    if uses_target_power_damage_cohort(effect):
        validate_target_power_damage_cohort(effect)
        return TARGET_POWER_DAMAGE_COHORT_DEPENDENCIES
    if uses_trample_bite(effect):
        validate_trample_bite(effect)
        return TRAMPLE_BITE_DEPENDENCIES
    if uses_source_power_opponent_damage(effect):
        validate_source_power_opponent_damage(effect)
        return (SOURCE_POWER_OPPONENT_DAMAGE_CAPABILITY,)
    if is_creature_self_damage_set(effect):
        validate_creature_self_damage_set(effect)
        return CREATURE_SELF_DAMAGE_SET_DEPENDENCIES
    if is_creature_controller_damage_set(effect):
        validate_creature_controller_damage_set(effect)
        return CREATURE_CONTROLLER_DAMAGE_SET_DEPENDENCIES
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
