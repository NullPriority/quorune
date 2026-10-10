from __future__ import annotations

"""Closed canonical ObjectQuery alternatives for private hand entry."""

from typing import Any, Mapping
from .object_predicate import ObjectQuerySpec
from .object_query import object_matches_query

PERMANENT_TYPES = ('artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker')
HISTORIC_QUERY_KIND = 'hand_entry_query_union'


def historic_hand_entry_queries() -> tuple[ObjectQuerySpec, ...]:
    return (
        ObjectQuerySpec(zones=('hand',), types_all=('artifact',)),
        ObjectQuerySpec(zones=('hand',), types_any=PERMANENT_TYPES, supertypes_all=('legendary',)),
        ObjectQuerySpec(zones=('hand',), types_all=('enchantment',), subtypes_all=('saga',)),
    )


def historic_hand_entry_query_descriptor() -> dict[str, Any]:
    return {
        'kind': HISTORIC_QUERY_KIND,
        'schema_version': 1,
        'alternatives': [query.canonical_dict() for query in historic_hand_entry_queries()],
    }


def _single_hand_query_is_closed(query: ObjectQuerySpec) -> bool:
    standard = ObjectQuerySpec(
        zones=('hand',), types_all=query.types_all, types_any=query.types_any,
        subtypes_all=query.subtypes_all, supertypes_all=query.supertypes_all,
        colors_any=query.colors_any, minimum_color_count=query.minimum_color_count,
    )
    if standard != query:
        return False
    if query.types_all==('land',):
        return not query.types_any and not query.subtypes_all and not query.colors_any and query.minimum_color_count is None and query.supertypes_all in {(),('basic',)}
    if query.types_all==('creature',):
        return not query.types_any and not query.subtypes_all and not query.supertypes_all and len(query.colors_any)<=2 and query.minimum_color_count in {None,2} and not(query.colors_any and query.minimum_color_count is not None)
    if query.types_all==('artifact',):
        return not query.types_any and query.subtypes_all in {(),('equipment',)} and not query.supertypes_all and not query.colors_any and query.minimum_color_count is None
    return query.types_any==PERMANENT_TYPES and not query.types_all and query.subtypes_all==('minotaur',) and not query.supertypes_all and not query.colors_any and query.minimum_color_count is None


def decode_hand_entry_queries(value: Mapping[str, Any]) -> tuple[ObjectQuerySpec, ...]:
    if not isinstance(value, Mapping):
        raise ValueError('Hand entry query must be an object')
    if value.get('kind') == HISTORIC_QUERY_KIND:
        if (
            set(value) != {'kind', 'schema_version', 'alternatives'}
            or type(value['schema_version']) is not int
            or value['schema_version'] != 1
            or not isinstance(value['alternatives'], (list, tuple))
        ):
            raise ValueError('Hand entry alternatives require the closed versioned schema')
        queries = tuple(ObjectQuerySpec.from_dict(raw) for raw in value['alternatives'])
        if queries != historic_hand_entry_queries():
            raise ValueError('Hand entry alternatives must be the canonical historic permanent union')
        return queries
    query = ObjectQuerySpec.from_dict(value)
    if not _single_hand_query_is_closed(query):
        raise ValueError('Private hand-entry query is outside the closed family')
    return (query,)


def hand_entry_matches(row, queries: tuple[ObjectQuerySpec, ...]) -> bool:
    return any(object_matches_query(row, query) for query in queries)
