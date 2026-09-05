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
    return state.to_dict() in (
        PermanentStatePredicateSpec(attacking=True).to_dict(),
        PermanentStatePredicateSpec(blocking=True).to_dict(),
    )


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
        or query.excluded_types
        or query.subtypes_any
        or query.excluded_subtypes
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

    qualifiers = sum(
        bool(value)
        for value in (
            query.subtypes_all,
            query.supertypes_all,
            query.colors_all,
            query.colorless is not None,
            query.token is not None,
        )
    )
    if query.state_predicate is not None:
        return (
            query.types_all == ("creature",)
            and qualifiers == 0
            and (
                query.exclude_ref is None
                or query.state_predicate.attacking is True
            )
        )
    if query.controller != "$controller":
        return (
            query.types_all == ("creature",)
            and qualifiers == 0
            and query.exclude_ref is None
        )
    if query.subtypes_all:
        return (
            query.types_all == ("creature",)
            and qualifiers == 1
            and len(query.subtypes_all) == 1
            and canonical_creature_subtype(query.subtypes_all[0])
            == query.subtypes_all[0]
        )
    if query.supertypes_all:
        return (
            query.types_all == ("creature",)
            and qualifiers == 1
            and query.supertypes_all == ("legendary",)
        )
    if query.colors_all:
        return (
            query.types_all == ("creature",)
            and qualifiers == 1
            and len(query.colors_all) == 1
            and query.colors_all[0] in "WUBRG"
        )
    if query.colorless is not None:
        return (
            query.types_all == ("creature",)
            and qualifiers == 1
            and query.colorless is True
        )
    if query.token is not None:
        return query.types_all == ("creature",) and qualifiers == 1
    return query.types_all in {
        (),
        ("artifact",),
        ("land",),
        ("creature",),
        ("artifact", "creature"),
        ("land", "creature"),
    }


__all__ = ["fixed_resolution_characteristic_query_is_closed"]
