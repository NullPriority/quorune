from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..compiler.fixed_library_selection_templates import (
    FIXED_LIBRARY_SELECTION_CAPABILITY,
    FIXED_LIBRARY_SELECTION_MECHANIC,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec


_EFFECT_FIELDS = {
    "op",
    "player",
    "look_count",
    "public_reveal",
    "selected_reveal",
    "selection_policy",
    "minimum",
    "maximum",
    "predicate_groups",
    "remainder_destination",
    "remainder_order",
}
_POLICIES = {
    "fixed_any",
    "up_to_matching",
    "all_matching",
    "optional_slots",
}


def _characteristic_predicate(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        spec = ObjectQuerySpec.from_dict(value)
    except (ObjectQueryError, TypeError, ValueError):
        return False
    return bool(
        spec.zones == ("library",)
        and spec.owner is None
        and spec.controller is None
        and not spec.excluded_controllers
        and not spec.types_all
        and not spec.subtypes_all
        and not spec.excluded_subtypes
        and not spec.colors_all
        and not spec.keywords_all
        and spec.token is None
        and spec.tapped is None
        and not spec.include_phased_out
        and spec.known_to_actor is None
        and spec.exclude_ref is None
        and spec.state_predicate is None
        and spec.minimum_color_count is None
        and bool(
            spec.types_any
            or spec.excluded_types
            or spec.subtypes_any
            or spec.supertypes_all
            or spec.colors_any
            or spec.colorless is not None
        )
    )


def fixed_library_selection_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize only one closed fixed controller library selection."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_LIBRARY_SELECTION_MECHANIC not in mechanics
        or len(effects) != 1
        or target_schema is not None
    ):
        return ()
    effect = effects[0]
    if set(effect) != _EFFECT_FIELDS:
        return ()
    look_count = effect.get("look_count")
    minimum = effect.get("minimum")
    maximum = effect.get("maximum")
    policy = effect.get("selection_policy")
    groups = effect.get("predicate_groups")
    if (
        effect.get("op") != "fixed_library_selection"
        or effect.get("player") != "$controller"
        or type(look_count) is not int
        or look_count <= 0
        or type(effect.get("public_reveal")) is not bool
        or type(effect.get("selected_reveal")) is not bool
        or policy not in _POLICIES
        or type(minimum) is not int
        or type(maximum) is not int
        or minimum < 0
        or maximum <= 0
        or minimum > maximum
        or maximum > look_count
        or effect.get("remainder_destination")
        not in {"graveyard", "library_bottom"}
        or effect.get("remainder_order") not in {"chosen", "random"}
        or not isinstance(groups, (list, tuple))
    ):
        return ()
    parsed_groups = tuple(groups)
    if any(
        not isinstance(group, (list, tuple))
        or not group
        or any(not _characteristic_predicate(value) for value in group)
        for group in parsed_groups
    ):
        return ()
    if policy == "fixed_any":
        valid_policy = not parsed_groups and minimum == maximum
    elif policy == "optional_slots":
        valid_policy = (
            len(parsed_groups) == maximum
            and minimum == 0
            and len(parsed_groups) > 1
        )
    else:
        valid_policy = len(parsed_groups) == 1 and (
            policy != "all_matching" or minimum == 0
        )
    return (FIXED_LIBRARY_SELECTION_CAPABILITY,) if valid_policy else ()


__all__ = ["fixed_library_selection_node_capabilities"]
