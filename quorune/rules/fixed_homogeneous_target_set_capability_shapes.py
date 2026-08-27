from __future__ import annotations

"""Capability closure for bounded homogeneous target-set effects."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.direct_target import DirectPermanentTargetSpec
from ..compiler.fixed_homogeneous_target_sets import (
    FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY,
    FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,
)
from .graveyard_card_targets import (
    GraveyardCardTargetError,
    OwnGraveyardCardTargetSpec,
    PublicGraveyardCardTargetSpec,
)
from .permanent_predicate_capability_shapes import (
    direct_target_predicate_capabilities,
)


_OPERATION_CAPABILITIES = {
    "destroy_targets": "permanent.destroy.effect",
    "exile_permanent_targets": "permanent.exile.effect",
    "return_permanent_targets_to_owner_hand": (
        "permanent.return.owner_hand"
    ),
    "return_graveyard_targets_to_owner_hand": (
        "card.return.own_graveyard_to_owner_hand"
    ),
    "exile_public_graveyard_targets": "card.exile.public_graveyard",
    "tap_targets": "permanent.tap.effect",
    "untap_targets": "permanent.untap.effect",
}
_OPERATION_MECHANICS = {
    "destroy_targets": "destroy",
    "exile_permanent_targets": "exile",
    "return_permanent_targets_to_owner_hand": "return-to-owner-hand",
    "return_graveyard_targets_to_owner_hand": "return-to-owner-hand",
    "exile_public_graveyard_targets": "exile",
    "tap_targets": "tap-and-untap",
    "untap_targets": "tap-and-untap",
}


def _target_bounds(schema: Mapping[str, Any]) -> tuple[int, int] | None:
    count_fields = set(schema).intersection({"count", "min", "max", "up_to"})
    if count_fields == {"count"}:
        count = schema["count"]
        return (
            (count, count)
            if type(count) is int and 2 <= count <= 6
            else None
        )
    if count_fields == {"min", "max"}:
        minimum = schema["min"]
        maximum = schema["max"]
        return (
            (minimum, maximum)
            if minimum == 1 and maximum == 2
            else None
        )
    if count_fields == {"up_to"}:
        maximum = schema["up_to"]
        return (
            (0, maximum)
            if type(maximum) is int and 1 <= maximum <= 6
            else None
        )
    return None


def _singular_schema(
    target_schema: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool] | None:
    if not isinstance(target_schema, Mapping):
        return None
    schema = dict(target_schema)
    bounds = _target_bounds(schema)
    if bounds is None:
        return None
    same_owner = schema.pop("same_owner", False)
    if type(same_owner) is not bool:
        return None
    for field in ("count", "min", "max", "up_to"):
        schema.pop(field, None)
    schema["count"] = 1
    return schema, same_owner


def fixed_homogeneous_target_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,
        "cr-115-targets",
    }.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    operation = effect.get("op")
    maximum = effect.get("maximum_targets")
    if (
        operation not in _OPERATION_CAPABILITIES
        or _OPERATION_MECHANICS[str(operation)] not in mechanics
        or set(effect) != {"op", "cards", "maximum_targets"}
        or effect.get("cards") != "$targets"
        or type(maximum) is not int
        or not 1 <= maximum <= 6
    ):
        return ()
    normalized = _singular_schema(target_schema)
    if normalized is None:
        return ()
    singular, same_owner = normalized
    bounds = _target_bounds(dict(target_schema or {}))
    if bounds is None or bounds[1] != maximum:
        return ()

    predicate_capabilities: tuple[str, ...] = ()
    if operation in {
        "destroy_targets",
        "exile_permanent_targets",
        "return_permanent_targets_to_owner_hand",
        "tap_targets",
        "untap_targets",
    }:
        if same_owner:
            return ()
        try:
            DirectPermanentTargetSpec.from_target_schema(singular)
        except (TypeError, ValueError):
            return ()
        predicate_capabilities = direct_target_predicate_capabilities(singular)
    elif operation == "return_graveyard_targets_to_owner_hand":
        if same_owner:
            return ()
        try:
            OwnGraveyardCardTargetSpec.from_target_schema(singular)
        except (GraveyardCardTargetError, TypeError):
            return ()
    else:
        try:
            PublicGraveyardCardTargetSpec.from_target_schema(singular)
        except (GraveyardCardTargetError, TypeError):
            return ()

    return (
        FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY,
        _OPERATION_CAPABILITIES[str(operation)],
        *predicate_capabilities,
        "target.revalidate_resolution",
    )


def is_closed_fixed_homogeneous_target_set_program(program: Any) -> bool:
    """Recognize one capability-closed bounded homogeneous target set."""

    required = set(
        fixed_homogeneous_target_set_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


__all__ = [
    "fixed_homogeneous_target_set_node_capabilities",
    "is_closed_fixed_homogeneous_target_set_program",
]
