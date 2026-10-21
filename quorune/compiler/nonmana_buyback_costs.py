from __future__ import annotations

"""Compile complete fixed nonmana Buyback cost clauses into typed prices."""

import re

from ..additional_cost_vocabulary import DISCARD_ONE_COST, SACRIFICE_ONE_COST
from ..cast_lifecycles import FixedCastLifecycleKind, FixedCastLifecycleSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from ..rules.casting_additional_cost_groups import FixedLifePaymentAdditionalCost
from ..rules.random_discard_additional_cost import FixedRandomDiscardAdditionalCost


def _count(word):
    return {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}.get(word, int(word) if word.isdecimal() else None)


def compile_nonmana_buyback(*, material_line, oracle_line, line_index):
    material = re.sub(r'\s*\([^()]*\)\.?$', '', ' '.join(material_line.strip().split())).strip()
    match = re.fullmatch(r'Buyback[—–-]\s*(?P<price>.+?)\.?', material, re.I)
    if match is None:return None
    price = match['price'];costs = None
    if pay := re.fullmatch(r'Pay (?P<life>[1-9][0-9]*) life(?P<random>, Discard a card at random)?', price, re.I):
        costs = [FixedLifePaymentAdditionalCost(int(pay['life'])).to_descriptor()]
        if pay['random']:costs.append(FixedRandomDiscardAdditionalCost().to_descriptor())
    elif discard := re.fullmatch(r'Discard (?P<count>a|one|two|three|four|five|[1-9][0-9]*) cards?', price, re.I):
        count = _count(discard['count'].casefold())
        if count is None or count > 99:return None
        costs = [FixedZoneChangeAdditionalCost(operation=DISCARD_ONE_COST, choice_field='discard_cards', count=count,
            predicate=ObjectQuerySpec(zones=('hand',), owner='$actor', known_to_actor=True)).to_descriptor()]
    elif sacrifice := re.fullmatch(r'Sacrifice (?P<count>a|one|two|three|four|five|[1-9][0-9]*) (?P<type>lands?|Plains|Islands?|Swamps?|Mountains?|Forests?)', price, re.I):
        count = _count(sacrifice['count'].casefold())
        if count is None or count > 99:return None
        basic = {'plains': 'plains', 'island': 'island', 'islands': 'island', 'swamp': 'swamp', 'swamps': 'swamp',
            'mountain': 'mountain', 'mountains': 'mountain', 'forest': 'forest', 'forests': 'forest'}.get(sacrifice['type'].casefold())
        costs = [FixedZoneChangeAdditionalCost(operation=SACRIFICE_ONE_COST, choice_field='sacrifice_cards', count=count,
            predicate=ObjectQuerySpec(zones=('battlefield',), controller='$actor', known_to_actor=True,
                types_all=('land',), subtypes_all=(basic,) if basic else ())).to_descriptor()]
    if costs is None:return None
    return FixedCastLifecycleSpec(ability_id=f'ab{line_index+1}', line_index=line_index, oracle_line=oracle_line,
        kind=FixedCastLifecycleKind.BUYBACK, cost_text=price,
        mana_cost=FrozenMap({k: 0 for k in ('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C')}),
        additional_costs=tuple(FrozenMap(cost) for cost in costs), schema_version=5)
