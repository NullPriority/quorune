from __future__ import annotations

"""Common optional count expansion before total-cost modifiers."""

from copy import deepcopy


def expand_repeated_mana_costs(options, schema, *, spec_type, field, count_field, label,
        suffix_name, response=None, hint=False):
    response = response or {}
    if field not in schema:
        return [] if count_field in response else options
    spec = spec_type.from_dict(schema[field])
    count = 1
    suffix = '+' + suffix_name + ':' + spec.instance_id
    if not hint and count_field in response:
        count = response[count_field]
        if type(count) is not int or count < 0:
            return []
        selected = response.get('cost_option')
        if count == 0:
            return options if selected in (None, '', *(row['id'] for row in options)) else []
        if selected and selected not in {row['id'] + suffix for row in options}:
            return []
    result = [] if not hint and count_field in response and not response.get('cost_option') else list(options)
    for option in options:
        paid = deepcopy(option)
        paid['id'] += suffix
        paid['label'] = str(option.get('label') or 'Cast') + ' with ' + label
        paid[field] = spec.payment_fact(count)
        choices = deepcopy(dict(paid.get('choice_schema') or {}))
        if 'field' in choices:
            choices = {choices['field']: {k:v for k,v in choices.items() if k != 'field'}}
        choices[count_field] = {'type': 'integer', 'label': label + ' payments',
            'minimum': 1, 'default': 1, 'required': True}
        paid['choice_schema'] = choices
        for symbol, amount in spec.requirements.items():
            paid['requirements'][symbol] += amount * count
        result.append(paid)
    return result
