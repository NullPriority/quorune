from __future__ import annotations

"""Whole-value aggregate grammar over the shared public battlefield query."""

import re

from ..object_predicate import ObjectQuerySpec
from ..public_characteristic_aggregate_model import PublicCharacteristicAggregateSpec
from ..scalar_effect_amount_model import ScalarAmountOrigin,ScalarEffectAmountSpec


_DISTINCT_QUERIES = {
    ('power', 'creatures you control'): ObjectQuerySpec(zones=('battlefield',), types_all=('creature',)),
    ('mana value', 'nonland permanents you control'): ObjectQuerySpec(zones=('battlefield',), excluded_types=('land',)),
    ('mana value', 'cards in your graveyard'): ObjectQuerySpec(zones=('graveyard',), token=False),
    ('mana value', 'nonland cards in your graveyard'): ObjectQuerySpec(zones=('graveyard',), token=False, excluded_types=('land',)),
    ('mana value', 'instant and sorcery cards in your graveyard'): ObjectQuerySpec(zones=('graveyard',), token=False,
                                                                               types_any=('instant', 'sorcery')),
}


def public_characteristic_aggregate(value:str):
    distinct = re.fullmatch(r'(?:the number of )?different (?P<field>power|mana value) among (?P<objects>.+)',
                            value.strip().rstrip('.'), re.I)
    if distinct is not None:
        field = distinct['field'].casefold()
        query = _DISTINCT_QUERIES.get((field, distinct['objects'].casefold()))
        if query is None:
            return None
        aggregate = PublicCharacteristicAggregateSpec(field.replace(' ', '_'), 'distinct_count',
                                                     'source_controller', query, schema_version=2)
        return ScalarEffectAmountSpec(ScalarAmountOrigin.PUBLIC_AGGREGATE, schema_version=3, aggregate=aggregate)
    match=re.fullmatch(r'(?:the )?(?P<reduction>total|greatest|highest) (?P<field>power|toughness) (?:among|of) (?P<objects>.+)',value.strip().rstrip('.'),re.I)
    if match is None:return None
    from .public_state_queries import fixed_characteristic_battlefield_query_subject
    parsed=fixed_characteristic_battlefield_query_subject(match['objects'])
    if parsed is None or parsed[2]:return None
    try:
        aggregate=PublicCharacteristicAggregateSpec(match['field'].casefold(),
            'sum' if match['reduction'].casefold()=='total' else 'maximum',parsed[0],parsed[1])
        return ScalarEffectAmountSpec(ScalarAmountOrigin.PUBLIC_AGGREGATE,schema_version=3,aggregate=aggregate)
    except (ValueError,TypeError):return None
