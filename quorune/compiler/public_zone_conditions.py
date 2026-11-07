from __future__ import annotations

import re

from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec, FixedPublicStateFact
from .fixed_numbers import fixed_number


_COUNT = r"zero|one|two|three|four|five|six|seven|eight|nine|ten|twenty|[0-9]+"
_GRAVEYARD_COUNT = re.compile(
    rf"a graveyard has (?P<has>{_COUNT}) or more cards in it"
    rf"|there are (?P<exists>{_COUNT}) or more cards in a single graveyard"
)
_LIBRARY_COUNT = re.compile(rf"a library has (?P<count>{_COUNT}) or fewer cards in it")


def public_zone_condition(text: str):
    normalized = ' '.join(text.casefold().split())
    parameter = None
    if normalized == 'an opponent has no cards in hand':
        fact = FixedPublicStateFact.OPPONENT_HAND_EMPTY
    elif normalized == 'an opponent owns a card in exile':
        fact = FixedPublicStateFact.OPPONENT_OWNS_EXILED_CARD
    else:
        library = _LIBRARY_COUNT.fullmatch(normalized)
        graveyard = _GRAVEYARD_COUNT.fullmatch(normalized)
        if library is not None:
            count = library["count"]
            fact = FixedPublicStateFact.ANY_LIBRARY_COUNT_AT_MOST
        elif graveyard is not None:
            count = graveyard["has"] or graveyard["exists"]
            fact = FixedPublicStateFact.ANY_GRAVEYARD_COUNT_AT_LEAST
        else:
            return None
        if count.isdecimal():
            count = count.lstrip("0") or "0"
        if len(count) > 10:
            return None
        amount = {'zero': 0, 'twenty': 20}.get(count)
        if amount is None:
            amount = fixed_number(count)
        if amount is None or not 0 <= amount <= 2147483647:
            return None
        parameter = str(amount)
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
        amount=1, fact=fact, fact_parameter=parameter, schema_version=4)
