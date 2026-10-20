from __future__ import annotations

"""Fixed named-counter payment from one controlled creature, compiled once."""

from dataclasses import replace
import re

from ..abilities import CostChoice
from ..rules.selected_counter_costs import SELECTED_PLUS_COUNTER_COST_KIND
from ..rules.activation_counter_cost import SourceCounterRemovalCost


def fixed_selected_counter_activation(ability):
    if (ability.zones != ('battlefield',) or ability.mana_ability or ability.complex_symbols
            or ability.untap_source or ability.discard_source or ability.sacrifice_source
            or ability.exile_source or ability.life_payment or ability.energy_payment
            or ability.random_discard_payment or ability.loyalty_delta is not None
            or ability.source_counter_removal_cost is not None or ability.choices
            or len(ability.uncompiled_costs) != 1):
        return ability
    clause = ' '.join(ability.uncompiled_costs[0].strip(' .').split())
    if not re.fullmatch(r'Remove (?:a|one|1) \+1/\+1 counter from a creature you control', clause, re.I):
        return ability
    choice = CostChoice(kind=SELECTED_PLUS_COUNTER_COST_KIND, count=1,
        zone='battlefield', card_type='creature')
    return replace(ability, choices=(choice,), uncompiled_costs=())


def fixed_any_source_counter_activation(ability):
    if (ability.zones != ('battlefield',) or ability.mana_ability
            or ability.untap_source or ability.discard_source or ability.sacrifice_source
            or ability.exile_source or ability.life_payment or ability.energy_payment
            or ability.random_discard_payment or ability.loyalty_delta is not None
            or ability.source_counter_removal_cost is not None or ability.choices
            or len(ability.uncompiled_costs) != 1):
        return ability
    clause = ' '.join(ability.uncompiled_costs[0].strip(' .').split())
    match = re.fullmatch(r'Remove (?P<count>a|one|two|1|2) (?P<noun>counters?) from this creature', clause, re.I)
    if match is None:
        return ability
    amount = 2 if match['count'].casefold() in {'two', '2'} else 1
    if match['noun'].casefold() != ('counter' if amount == 1 else 'counters'):
        return ability
    return replace(ability, source_counter_removal_cost=SourceCounterRemovalCost(None, amount, 2), uncompiled_costs=())
