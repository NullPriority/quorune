from __future__ import annotations

"""Bind selected Spree prices and target groups before total-cost modifiers."""

from copy import deepcopy
from itertools import combinations

from ...spree_model import SPREE_FIXED_MECHANIC, SpreeModeCosts
from ..modal_selection import canonical_modes


def with_spree_mode_cost_options(program, schema, options, response, *, hint):
    if 'spree' not in schema:
        return options
    try:
        prices = SpreeModeCosts.from_dict(schema['spree'])
        prices.validate_schema(program.target_schema)
        if SPREE_FIXED_MECHANIC not in program.coverage or schema['spree'] != program.cost_schema.get('spree'):
            return []
        if response.get('modes'):
            selections = (canonical_modes(program.target_schema, response['modes']),)
        elif hint:
            ids = tuple(prices.mode_costs)
            selections = tuple(group for count in range(1, len(ids) + 1) for group in combinations(ids, count))
        else:
            return []
    except (TypeError, ValueError, KeyError, AttributeError):
        return []
    result = []
    for option in options:
        for selected in selections:
            choice = deepcopy(option)
            for mode_id in selected:
                for symbol, amount in prices.mode_costs[mode_id].items():
                    choice['requirements'][symbol] += amount
            choice['id'] = option['id'] + '+spree:' + ','.join(selected)
            choice['spree_base_option_id'] = option['id']
            choice['spree_modes'] = list(selected)
            choice['label'] = str(option.get('label') or 'Cast') + ': ' + ', '.join(mode.replace('_', ' ') for mode in selected)
            choice['target_schema'] = {'mode_count': len(selected), 'min_modes': len(selected), 'max_modes': len(selected),
                'modes': {mode_id: deepcopy(program.target_schema['modes'][mode_id]) for mode_id in selected}}
            result.append(choice)
    return result


def default_cast_cost_option_id(options):
    normal = next((option.option_id for option in options if option.option_id == 'normal'), None)
    if normal is not None:
        return normal
    normal_spree = next((option.option_id for option in options if option.to_dict().get('spree_base_option_id') == 'normal'), None)
    if normal_spree is not None:
        return normal_spree
    return options[0].option_id if len(options) == 1 else ''
