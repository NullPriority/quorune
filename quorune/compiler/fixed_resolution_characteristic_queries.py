from __future__ import annotations

"""Closed queries for resolution-locked characteristic effects."""

from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec


_PLAYER_TARGET_SCHEMA = {
    "zones": ["player"],
    "categories": ["player"],
    "player_relation": "any",
    "count": 1,
}


def _public_state_is_closed(
    state: PermanentStatePredicateSpec | None,
) -> bool:
    if state is None:
        return True
    supported = (
        PermanentStatePredicateSpec(attacking=True),
        PermanentStatePredicateSpec(blocking=True),
        PermanentStatePredicateSpec(enchanted=True),
        PermanentStatePredicateSpec(equipped=True),
        PermanentStatePredicateSpec(modified=True),
        PermanentStatePredicateSpec(
            counter_name="+1/+1",
            minimum_counter_count=1,
        ),
    )
    return state.to_dict() in tuple(value.to_dict() for value in supported)


def _target_schema_is_closed(
    target_schema: Mapping[str, Any] | None,
) -> bool:
    return isinstance(target_schema, Mapping) and dict(target_schema) == (
        _PLAYER_TARGET_SCHEMA
    )


def fixed_resolution_characteristic_query_is_closed(
    query: ObjectQuerySpec,
    *,
    target_schema: Mapping[str, Any] | None,
) -> bool:
    """Validate one shared controlled or public resolution-time set."""

    if (
        query.zones != ("battlefield",)
        or query.owner is not None
        or query.types_any
        or query.colors_any
        or query.keywords_all
        or query.tapped is not None
        or query.include_phased_out
        or query.known_to_actor is not None
        or not _public_state_is_closed(query.state_predicate)
        or query.exclude_ref not in {None, "$source"}
    ):
        return False
    if query.controller == "$controller":
        if target_schema is not None or query.excluded_controllers:
            return False
    elif query.controller == "$target.0":
        if query.excluded_controllers or not _target_schema_is_closed(
            target_schema
        ):
            return False
    elif query.controller is None:
        if target_schema is not None or query.excluded_controllers not in {
            (),
            ("$controller",),
        }:
            return False
    else:
        return False

    if any(
        canonical_creature_subtype(value) != value
        for value in (
            *query.subtypes_all,
            *query.subtypes_any,
            *query.excluded_subtypes,
        )
    ):
        return False
    if (
        not set(query.types_all)
        <= {"artifact", "creature", "enchantment", "land", "planeswalker"}
        or not set(query.excluded_types)
        <= {"artifact", "creature", "enchantment", "land", "planeswalker"}
        or not set(query.supertypes_all) <= {"legendary", "snow"}
        or any(value not in "WUBRG" for value in query.colors_all)
        or any(value not in "WUBRG" for value in query.colors_any)
        or query.minimum_color_count not in {None, 2}
        or query.colorless not in {None, True}
    ):
        return False
    qualifier_groups = sum(
        bool(value)
        for value in (
            query.excluded_types,
            query.subtypes_all,
            query.subtypes_any,
            query.excluded_subtypes,
            query.supertypes_all,
            query.colors_all,
            query.colors_any,
            query.minimum_color_count is not None,
            query.colorless is not None,
            query.token is not None,
            query.state_predicate is not None,
        )
    )
    if qualifier_groups > 2:
        return False
    if query.exclude_ref is not None and not (
        query.controller == "$controller"
        or query.state_predicate is not None
    ):
        return False
    return bool(
        query.types_all
        or qualifier_groups
        or query.controller is not None
    )


__all__ = ["fixed_resolution_characteristic_query_is_closed"]
