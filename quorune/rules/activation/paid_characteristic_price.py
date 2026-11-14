"""Closed selected-creature price shared by compiler and activation rules."""
from collections.abc import Mapping
from ...activation_cost_choice import CostChoice
from ...replacement.immutable import FrozenMap


def activation_creature_sacrifice_choice(cost):
    if not isinstance(cost, Mapping):
        choices = getattr(cost, 'choices', None)
        if not isinstance(choices, (tuple, list)) or any(not isinstance(c, CostChoice) for c in choices):
            return None
        cost = {**{k: getattr(cost, k, None) for k in ('uncompiled_costs', 'complex_symbols', 'mana', 'tap_source',
            'discard_source', 'sacrifice_source', 'exile_source', 'untap_source', 'life_payment',
            'energy_payment', 'source_counter_removal_cost', 'loyalty_delta', 'waterbend',
            'random_discard_payment', 'announced_x_symbols')}, 'choices': [c.compact() for c in choices]}
    if not isinstance(cost, Mapping) or cost.get('uncompiled_costs') or cost.get('complex_symbols'):
        return None
    mana = cost.get('mana')
    if (type(cost.get('tap_source')) is not bool or not isinstance(mana, Mapping)
            or set(mana) - {'W', 'U', 'B', 'R', 'G', 'C', 'GENERIC'}
            or any(type(n) is not int or n < 0 for n in mana.values())):
        return None
    if any(cost.get(k) is not False for k in ('discard_source', 'sacrifice_source', 'exile_source', 'untap_source')):
        return None
    if any(cost.get(k) for k in ('life_payment', 'energy_payment', 'source_counter_removal_cost', 'loyalty_delta',
            'waterbend', 'random_discard_payment', 'announced_x_symbols')):
        return None
    choices = cost.get('choices')
    if not isinstance(choices, (list, tuple)) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        return None
    value = choices[0]
    if set(value) - {'k', 'n', 'z', 'q', 'other'} or not {'k', 'n', 'z', 'q'} <= set(value):
        return None
    if type(value.get('other', 0)) is not int or value.get('other', 0) not in (0, 1):
        return None
    try:
        choice = CostChoice(value['k'], value['n'], value['z'], another=bool(value.get('other')),
            predicate=FrozenMap(value['q']))
        price = choice.fixed_zone_change_cost()
    except (TypeError, ValueError, KeyError):
        return None
    if (price is None or price.operation != 'sacrifice_one' or choice.count != 1
            or not ('creature' in price.predicate.types_all or price.predicate.types_any == ('creature',))):
        return None
    return choice
