from __future__ import annotations

"""Closed controller-bound public conditions at a resolution instruction."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .characteristic_fragments import CharacteristicQuantityScope
from .continuous_conditions import (
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
)
from .replacement.immutable import FrozenMap


RESOLUTION_CONDITION_OPERATION = "apply_if_public_condition"
RESOLUTION_CONDITION_MECHANIC = "fixed-resolution-public-condition"
RESOLUTION_CONDITION_CAPABILITY = "resolution.effect.public_condition"
CAST_FACT_CONDITION_CAPABILITY = 'resolution.effect.fixed_cast_fact'
CAST_FACT_CONDITION_MECHANIC = 'fixed-cast-fact-condition'
_DERIVED_RESULT_MECHANICS = frozenset({
    "declared-effect-amount", "public-query-effect-amount", "scalar-effect-amount",
})
_DERIVED_VALUE_KINDS = frozenset({
    "declared_effect_amount", "public_query_effect_amount", "scalar_effect_amount",
})


def _contains_derived_result_value(value: Any) -> bool:
    if isinstance(value, str):
        return value == "$source" or value.startswith("$source.")
    if isinstance(value, Mapping):
        return value.get("op") == "delayed_trigger" or value.get("kind") in _DERIVED_VALUE_KINDS or any(
            _contains_derived_result_value(child) for child in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_derived_result_value(child) for child in value)
    return False


def resolution_result_is_fixed(effects: Any, mechanic_ids: Any) -> bool:
    """Keep unproved quantities and source bindings outside this fixed slice."""

    return not _DERIVED_RESULT_MECHANICS.intersection(mechanic_ids) and not _contains_derived_result_value(effects)


def cast_fact_result_is_fixed(effects: Any, mechanic_ids: Any) -> bool:
    """A counter instruction's source attribution is not a result quantity."""
    values = tuple(
        {key: value for key, value in effect.items() if key != 'source'}
        if effect.get('op') in {'place_counters_on_set', 'place_counter_set'} and effect.get('source') == '$source'
        else effect for effect in effects
    )
    return resolution_result_is_fixed(values, mechanic_ids)


@dataclass(frozen=True, slots=True)
class ResolutionConditionBinding:
    """The resolving controller, independent of a source's current incarnation."""

    controller: str
    object_id: str = "resolution-controller"
    counters: FrozenMap = field(default_factory=FrozenMap)
    entered_battlefield_turn_sequence: int = 0


@dataclass(frozen=True, slots=True)
class KickedCastCondition:
    kind: str = 'kicked_cast'
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version, 'kind': self.kind}


def resolution_condition_is_closed(condition: FixedPublicStateConditionSpec) -> bool:
    """Exclude object-relative bindings; this boundary binds only the controller."""

    if condition.kind in {
        FixedPublicStateConditionKind.SOURCE_ENTERED_THIS_TURN,
        FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST,
        FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
        FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY,
    }:
        return False
    if condition.fact is not None and (
        condition.fact.value.startswith("source_")
        or condition.fact.value == "controller_other_creatures_entered_this_turn"
    ):
        return False
    quantity = condition.quantity
    return quantity is None or not (
        quantity.schema_version != 1
        or quantity.scope is CharacteristicQuantityScope.ATTACHED_TO_SOURCE
        or quantity.scope is CharacteristicQuantityScope.SOURCE_COUNTER
        or quantity.exclude_source
        or quantity.exclude_attached_object
    )


def validate_resolution_condition_instruction(
    effect: Mapping[str, Any],
) -> tuple[FixedPublicStateConditionSpec | KickedCastCondition, tuple[Mapping[str, Any], ...]]:
    if not isinstance(effect, Mapping):
        raise ValueError("Resolution condition instructions require an object")
    expected_fields = {
        "op", "player", "condition", "effects",
        "mechanic_ids", "prefix_mechanic_ids",
    }
    cast_fact = effect.get('schema_version') == 2
    if cast_fact:
        expected_fields |= {'schema_version', 'cast_fact'}
    if (
        not isinstance(effect, Mapping)
        or set(effect) != expected_fields
        or effect.get("op") != RESOLUTION_CONDITION_OPERATION
    ):
        raise ValueError("Resolution condition instructions have a closed schema")
    if type(effect.get("player")) is not str or not effect["player"]:
        raise ValueError("Resolution conditions require a controller binding")
    for name in ("mechanic_ids", "prefix_mechanic_ids"):
        values = effect[name]
        if (
            not isinstance(values, (list, tuple))
            or any(type(value) is not str or not value for value in values)
            or len(set(values)) != len(values)
            or RESOLUTION_CONDITION_MECHANIC in values
        ):
            raise ValueError("Resolution condition component mechanics are malformed")
    if not effect["mechanic_ids"]:
        raise ValueError("Resolution condition result mechanics must be present")
    if "fixed-next-turn-upkeep-draw" in (*effect["mechanic_ids"], *effect["prefix_mechanic_ids"]):
        raise ValueError("Delayed draw remains outside conditional programs")
    if cast_fact:
        if type(effect['schema_version']) is not int or effect['condition'] != KickedCastCondition().to_dict() or not (
            type(effect['cast_fact']) is bool or effect['cast_fact'] == '$context.kicked'
        ):
            raise ValueError('Cast fact conditions require one sealed kicked marker')
        condition = KickedCastCondition()
    else:
        condition = FixedPublicStateConditionSpec.from_dict(effect["condition"])
    if not cast_fact and not resolution_condition_is_closed(condition):
        raise ValueError("Object-relative resolution conditions are unsupported")
    nested = effect["effects"]
    if not isinstance(nested, (list, tuple)) or not 1 <= len(nested) <= 8 or any(
        not isinstance(value, Mapping) or value.get("op") == RESOLUTION_CONDITION_OPERATION
        for value in nested
    ):
        raise ValueError("Resolution conditions require a bounded nonnested effect program")
    if not (cast_fact_result_is_fixed(nested, effect["mechanic_ids"]) if cast_fact else resolution_result_is_fixed(nested, effect["mechanic_ids"])):
        raise ValueError("Derived or source-relative results require a separate binding and timing boundary")
    return condition, tuple(nested)
