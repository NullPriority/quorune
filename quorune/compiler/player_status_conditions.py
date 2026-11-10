from __future__ import annotations

"""Closed public player-status conditions and Max Speed prefix parsing."""

import re
from ..continuous_conditions import FixedPublicStateConditionKind,FixedPublicStateConditionSpec,FixedPublicStateFact
from ..dungeon_catalog import DUNGEON_CATALOG


def _dungeon_status_condition(material: str):
    fact = None
    parameter = None
    if material == "you have the initiative":
        fact = FixedPublicStateFact.CONTROLLER_HAS_THE_INITIATIVE
    elif material in {"you've completed a dungeon", "you have completed a dungeon"}:
        fact = FixedPublicStateFact.CONTROLLER_COMPLETED_A_DUNGEON
    else:
        named = re.fullmatch(r"you (?:haven't|have not) completed (.+)", material)
        if named is not None:
            dungeon = next((key for key, definition in DUNGEON_CATALOG.items()
                            if definition.source_name.split(" // ", 1)[0].casefold() == named[1]), None)
            if dungeon is not None:
                fact = FixedPublicStateFact.CONTROLLER_HAS_NOT_COMPLETED_NAMED_DUNGEON
                parameter = dungeon.value
    if fact is None:
        return None
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
                                         amount=1, schema_version=4, fact=fact, fact_parameter=parameter)


def fixed_player_status_condition(normalized):
    material = normalized.casefold().replace("’", "'")
    if material=="you have the city's blessing":
        return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_HAS_CITYS_BLESSING)
    if dungeon_status := _dungeon_status_condition(material):
        return dungeon_status
    if normalized.casefold() in {"you're the monarch","you are the monarch"}:return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH)
    poisoned=re.fullmatch(r'an opponent (?:is poisoned|has (?P<count>three|[0-9]+) or more poison counters)',normalized,re.I)
    if poisoned is None:return None
    token=poisoned.group('count');amount=3 if token and token.casefold()=='three' else int(token) if token else 1
    assert amount>0
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.OPPONENT_POISON_COUNTER_AT_LEAST,amount=amount)


def max_speed_condition_parts(text):
    match=re.fullmatch(r'Max speed\s*[—�]\s*(?P<body>.+)',text,re.I)
    return (FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_MAX_SPEED),match['body']) if match is not None else None
