from __future__ import annotations

"""Closed public player-status conditions and Max Speed prefix parsing."""

import re
from ..continuous_conditions import FixedPublicStateConditionKind,FixedPublicStateConditionSpec


def fixed_player_status_condition(normalized):
    if normalized.casefold().replace('’',"'")=="you have the city's blessing":
        return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_HAS_CITYS_BLESSING)
    if normalized.casefold() in {"you're the monarch","you are the monarch"}:return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH)
    poisoned=re.fullmatch(r'an opponent (?:is poisoned|has (?P<count>three|[0-9]+) or more poison counters)',normalized,re.I)
    if poisoned is None:return None
    token=poisoned.group('count');amount=3 if token and token.casefold()=='three' else int(token) if token else 1
    assert amount>0
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.OPPONENT_POISON_COUNTER_AT_LEAST,amount=amount)


def max_speed_condition_parts(text):
    match=re.fullmatch(r'Max speed\s*[—�]\s*(?P<body>.+)',text,re.I)
    return (FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_MAX_SPEED),match['body']) if match is not None else None
