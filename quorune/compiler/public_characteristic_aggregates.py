from __future__ import annotations

"""Whole-value aggregate grammar over the shared public battlefield query."""

import re

from ..public_characteristic_aggregate_model import PublicCharacteristicAggregateSpec
from ..scalar_effect_amount_model import ScalarAmountOrigin,ScalarEffectAmountSpec


def public_characteristic_aggregate(value:str):
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
