from __future__ import annotations

"""Bind selected Spree prices and target groups before total-cost modifiers."""

from copy import deepcopy
from itertools import combinations

from ...spree_model import MODE_MANA_PRICE_FIELDS, fixed_mode_mana_costs
from ..modal_selection import canonical_modes


def with_spree_mode_cost_options(program, schema, options, response, *, hint):
    if not MODE_MANA_PRICE_FIELDS.intersection(schema):
        return options
    try:
        prices = fixed_mode_mana_costs(schema)
        prices.validate_schema(program.target_schema)
        if prices.mechanic not in program.coverage or schema[prices.field] != program.cost_schema.get(prices.field):
            return []
        if response.get('modes'):
            selections = (canonical_modes(program.target_schema, response['modes']),)
        elif hint:
            ids = tuple(prices.mode_costs)
            selections = tuple(group for count in range(1, prices.maximum_modes + 1) for group in combinations(ids, count))
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
            field = prices.field
            choice['id'] = option['id'] + f'+{field}:' + ','.join(selected)
            choice[f'{field}_base_option_id'] = option['id']
            choice[f'{field}_modes'] = list(selected)
            labels = [prices.mode_labels[mode] if prices.mode_labels is not None else mode.replace('_', ' ')
                      for mode in selected]
            choice['label'] = str(option.get('label') or 'Cast') + ': ' + ', '.join(labels)
            choice['target_schema'] = {'mode_count': len(selected), 'min_modes': len(selected), 'max_modes': len(selected),
                'modes': {mode_id: deepcopy(program.target_schema['modes'][mode_id]) for mode_id in selected}}
            result.append(choice)
    return result


def default_cast_cost_option_id(options):
    normal = next((option.option_id for option in options if option.option_id == 'normal'), None)
    if normal is not None:
        return normal
    normal_spree = next((option.option_id for option in options if any(option.to_dict().get(key)=='normal'
        for key in ('spree_base_option_id', 'tiered_base_option_id','entwine_base_option_id','escalate_base_option_id',
                    'pawprint_base_option_id', 'repeatable_mode_base_option_id'))), None)
    if normal_spree is not None:
        return normal_spree
    return options[0].option_id if len(options) == 1 else ''
