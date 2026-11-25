from __future__ import annotations

"""Closed public player-status conditions and Max Speed prefix parsing."""

from . import regex_working_set as re
from ..continuous_conditions import FixedPublicStateConditionKind,FixedPublicStateConditionSpec,FixedPublicStateFact
from ..dungeon_catalog import DUNGEON_CATALOG
from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec


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


def _starting_life_condition(material):
    scopes = {
        "an opponent's life total is less than half their starting life total":
            CharacteristicQuantityScope.OPPONENTS_BELOW_HALF_STARTING_LIFE,
        "a player's life total is less than or equal to half their starting life total":
            CharacteristicQuantityScope.PLAYERS_AT_OR_BELOW_HALF_STARTING_LIFE,
    }
    scope = scopes.get(material)
    if scope is None:
        return None
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, amount=1, schema_version=2,
        quantity=CharacteristicQuantitySpec(scope, schema_version=4),
    )


def fixed_player_status_condition(normalized):
    material = normalized.casefold().replace("’", "'")
    if starting_life := _starting_life_condition(material):
        return starting_life
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
