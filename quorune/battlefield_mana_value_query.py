from __future__ import annotations

"""Explicit live permanent mana-value bounds; graveyard wire shapes stay separate."""

PERMANENT_MANA_VALUE_QUERY_CAPABILITY = 'state_query.permanent.mana_value_bound'


def permanent_mana_value_matches(row, query):
    return query.permanent_mana_value_max is None or row.mana_value <= query.permanent_mana_value_max


def validate_permanent_mana_value_query(query):
    from .object_predicate import ObjectQueryError

    if (type(query.permanent_mana_value_max) is not int
            or not 0 <= query.permanent_mana_value_max <= 20
            or query.zones != ('battlefield',) or 'creature' not in query.types_all
            or query.mana_value_max is not None or query.any_of
            or query.numeric_characteristic is not None or query.known_to_actor is not None):
        raise ObjectQueryError('Permanent mana-value bounds require public battlefield creatures and a bound from zero through twenty')
