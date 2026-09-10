from __future__ import annotations

"""Capability closure for fixed single-target reanimation."""

from collections.abc import Iterable, Mapping, Sequence

from ..compiler.reanimation_templates import (
    FIXED_TARGET_REANIMATION_CAPABILITY,
    FIXED_TARGET_REANIMATION_MECHANIC,
)
from ..effect_contracts import REANIMATE_OPERATION
from ..targets import TargetGroup
from ..target_numeric import (
    TargetNumericCharacteristic,
    TargetNumericComparison,
)


_PERMANENT_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)


def fixed_target_reanimation_covered_mechanics(
    capability_ids: Iterable[str],
) -> tuple[str, ...]:
    if FIXED_TARGET_REANIMATION_CAPABILITY not in set(capability_ids):
        return ()
    return (FIXED_TARGET_REANIMATION_MECHANIC, "cr-115-targets")


def fixed_target_reanimation_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize exactly one closed graveyard-to-battlefield target move."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_TARGET_REANIMATION_MECHANIC not in mechanics
        or "cr-115-targets" not in mechanics
        or len(effects) != 1
        or not isinstance(target_schema, Mapping)
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card", "controller", "tapped"}
        or effect.get("op") != REANIMATE_OPERATION
        or effect.get("card") != "$target.0"
        or effect.get("controller") not in {"$controller", "$target.owner.0"}
        or type(effect.get("tapped")) is not bool
    ):
        return ()
    try:
        target = TargetGroup.from_mapping(target_schema)
    except (TypeError, ValueError):
        return ()
    if (
        target.zones != ("graveyard",)
        or target.categories != ("card",)
        or target.owner_relation not in {"any", "you", "opponent"}
        or target.controller_relation != "any"
        or target.min_targets not in {0, 1}
        or target.max_targets != 1
        or not set((*target.types_any, *target.types_all)).intersection(
            _PERMANENT_TYPES
        )
        or set(target.types_none) - {"land"}
        or target.keywords_all
        or target.keywords_none
        or target.colors_any
        or target.colors_all
        or target.colors_none
        or target.colorless is not None
        or target.color_count_min is not None
        or target.color_count_equal is not None
        or target.damage_history is not None
        or target.state_predicate is not None
    ):
        return ()
    numeric = target.numeric_characteristic
    if numeric is not None and (
        numeric.characteristic is not TargetNumericCharacteristic.POWER
        or numeric.comparison is not TargetNumericComparison.AT_MOST
        or set(target.types_any) != {"creature"}
    ):
        return ()
    forms = tuple(form.to_dict() for form in target.characteristic_forms_any)
    if forms not in (
        (),
        (
            {
                "types_all": ["creature"],
                "subtypes_any": [],
                "supertypes_any": [],
            },
            {
                "types_all": [],
                "subtypes_any": ["vehicle"],
                "supertypes_any": [],
            },
        ),
        (
            {
                "types_all": ["creature"],
                "subtypes_any": [],
                "supertypes_any": [],
            },
            {
                "types_all": [],
                "subtypes_any": ["spacecraft"],
                "supertypes_any": [],
            },
        ),
    ):
        return ()
    return (FIXED_TARGET_REANIMATION_CAPABILITY,)


__all__ = [
    "fixed_target_reanimation_covered_mechanics",
    "fixed_target_reanimation_node_capabilities",
]
