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


@dataclass(frozen=True, slots=True)
class ResolutionConditionBinding:
    """The resolving controller, independent of a source's current incarnation."""

    controller: str
    object_id: str = "resolution-controller"
    counters: FrozenMap = field(default_factory=FrozenMap)
    entered_battlefield_turn_sequence: int = 0


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
) -> tuple[FixedPublicStateConditionSpec, tuple[Mapping[str, Any], ...]]:
    expected_fields = {
        "op", "player", "condition", "effects",
        "mechanic_ids", "prefix_mechanic_ids",
    }
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
    condition = FixedPublicStateConditionSpec.from_dict(effect["condition"])
    if not resolution_condition_is_closed(condition):
        raise ValueError("Object-relative resolution conditions are unsupported")
    nested = effect["effects"]
    if not isinstance(nested, (list, tuple)) or not 1 <= len(nested) <= 8 or any(
        not isinstance(value, Mapping) or value.get("op") == RESOLUTION_CONDITION_OPERATION
        for value in nested
    ):
        raise ValueError("Resolution conditions require a bounded nonnested effect program")
    return condition, tuple(nested)
