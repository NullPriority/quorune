from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..compiler.library_search_templates import (
    FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
    FIXED_LIBRARY_SEARCH_MECHANIC_ID,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec


_SELECTOR_FIELDS = frozenset(
    {"types", "types_any", "subtypes_any", "supertypes", "colors_any"}
)
_PERMANENT_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)
FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID = "library.search.fixed_type_to_hand"
_FIXED_TYPECYCLING_SELECTORS = (
    {"types": ["land"], "supertypes": ["basic"]},
    *(
        {"types": ["land"], "subtypes_any": [subtype]}
        for subtype in ("plains", "island", "swamp", "mountain", "forest")
    ),
    {"types": ["artifact", "land"]},
    {"subtypes_any": ["wizard"]},
    {"subtypes_any": ["sliver"]},
)


def _query(selector: object) -> ObjectQuerySpec | None:
    if (
        not isinstance(selector, Mapping)
        or not selector
        or not set(selector).issubset(_SELECTOR_FIELDS)
    ):
        return None
    try:
        query = ObjectQuerySpec(
            zones=("library",),
            types_all=tuple(selector.get("types") or ()),
            types_any=tuple(selector.get("types_any") or ()),
            subtypes_any=tuple(selector.get("subtypes_any") or ()),
            supertypes_all=tuple(selector.get("supertypes") or ()),
            colors_any=tuple(selector.get("colors_any") or ()),
        )
    except (ObjectQueryError, TypeError):
        return None
    if not (
        query.types_all
        or query.types_any
        or query.subtypes_any
        or query.supertypes_all
        or query.colors_any
    ):
        return None
    if not set((*query.types_all, *query.types_any)).issubset(_PERMANENT_TYPES):
        return None
    return query


def fixed_library_search_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one compiler-owned fixed search-to-battlefield instruction."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_LIBRARY_SEARCH_MECHANIC_ID not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    required = {
        "op",
        "zone",
        "selector",
        "count",
        "destination",
        "shuffle_after",
    }
    if set(effect) not in (required, {*required, "enters_tapped_override"}):
        return ()
    count = effect.get("count")
    if not isinstance(count, Mapping) or set(count) != {"minimum", "maximum"}:
        return ()
    minimum = count.get("minimum")
    maximum = count.get("maximum")
    if (
        type(minimum) is not int
        or type(maximum) is not int
        or not 0 <= minimum <= maximum <= 10
        or maximum == 0
        or minimum not in {0, maximum}
    ):
        return ()
    query = _query(effect.get("selector"))
    if (
        effect.get("op") != "search"
        or effect.get("zone") != "library"
        or effect.get("destination") != "battlefield"
        or effect.get("shuffle_after") is not True
        or effect.get("enters_tapped_override", True) is not True
        or query is None
    ):
        return ()
    if maximum > 1 and not (
        effect.get("enters_tapped_override") is True
        and query.types_all == ("land",)
        and not query.types_any
    ):
        return ()
    return (FIXED_LIBRARY_SEARCH_CAPABILITY_ID,)


def fixed_type_to_hand_search_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize only the closed fixed Typecycling search instruction."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        "cycling" not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if set(effect) != {
        "op",
        "zone",
        "selector",
        "count",
        "destination",
        "reveal",
        "shuffle_after",
    }:
        return ()
    count = effect.get("count")
    selector = effect.get("selector")
    if (
        effect.get("op") != "search"
        or effect.get("zone") != "library"
        or effect.get("destination") != "hand"
        or effect.get("reveal") is not True
        or effect.get("shuffle_after") is not True
        or not isinstance(count, Mapping)
        or dict(count) != {"minimum": 1, "maximum": 1}
        or not isinstance(selector, Mapping)
        or dict(selector) not in _FIXED_TYPECYCLING_SELECTORS
    ):
        return ()
    return (FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID,)


__all__ = [
    "FIXED_LIBRARY_SEARCH_CAPABILITY_ID",
    "FIXED_LIBRARY_SEARCH_MECHANIC_ID",
    "FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID",
    "fixed_library_search_node_capabilities",
    "fixed_type_to_hand_search_node_capabilities",
]
