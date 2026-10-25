from __future__ import annotations

import re

from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec, FixedPublicStateFact
from .fixed_numbers import fixed_number


def public_zone_condition(text: str):
    normalized = ' '.join(text.casefold().split())
    parameter = None
    if normalized == 'an opponent has no cards in hand':
        fact = FixedPublicStateFact.OPPONENT_HAND_EMPTY
    elif normalized == 'an opponent owns a card in exile':
        fact = FixedPublicStateFact.OPPONENT_OWNS_EXILED_CARD
    else:
        match = re.fullmatch(r'a library has (?P<count>zero|one|two|three|four|five|six|seven|eight|nine|ten|twenty|[0-9]+) or fewer cards in it', normalized)
        if match is None:
            return None
        amount = {'zero': 0, 'twenty': 20}.get(match['count'])
        if amount is None:
            amount = fixed_number(match['count'])
        if amount is None or not 0 <= amount <= 2147483647:
            return None
        fact = FixedPublicStateFact.ANY_LIBRARY_COUNT_AT_MOST
        parameter = str(amount)
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
        amount=1, fact=fact, fact_parameter=parameter, schema_version=4)
