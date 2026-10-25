from __future__ import annotations

from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..continuous_conditions import FixedPublicStateConditionKind as Kind, FixedPublicStateConditionSpec as Condition, FixedPublicStateFact
from ..object_predicate import ObjectQuerySpec


def conditional_recipient_condition(text: str):
    normalized = ' '.join(text.casefold().split())
    if normalized == 'you control your commander':
        return Condition(Kind.PUBLIC_FACT_AT_LEAST, amount=1,
            fact=FixedPublicStateFact.CONTROLLER_CONTROLS_OWN_COMMANDER, schema_version=4)
    if normalized == 'you control exactly one creature':
        quantity = CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=('battlefield',), types_all=('creature',)))
        return Condition(Kind.ALL, schema_version=6, terms=(
            Condition(Kind.QUERY_COUNT_AT_LEAST, amount=1, quantity=quantity, schema_version=2),
            Condition(Kind.QUERY_COUNT_AT_MOST, amount=1, quantity=quantity, schema_version=2)))
    return None
