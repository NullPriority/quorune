from __future__ import annotations

"""Current admitted cost lookup and complete-total optional fee expansion."""

from copy import deepcopy

from ...card_programs.admission import program_has_complete_card_program_admission
from ...paid_entry_copy_model import (
    PAID_ENTRY_COPY_FIELD, PAID_ENTRY_COPY_HANDLER, PaidEntryCopySpec,
    validate_paid_entry_copy_fact,
)
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry


def _compiled_spec(host, card):
    record = host.card_record(card)
    if record is None or record.layout != 'normal' or record.faces or card.annotations.get('copy_overrides') is not None:
        return None
    registry = default_cast_cost_component_registry()
    found = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone='all', event='cast.cost'):
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') == PAID_ENTRY_COPY_HANDLER:
                found.extend(registry.lower(descriptor, None))
    return found[0] if len(found) == 1 else None


def with_paid_entry_copy_cost_schema(host, card, schema, *, suppress_source_costs):
    spec = None if suppress_source_costs else _compiled_spec(host, card)
    result = deepcopy(dict(schema))
    if spec is not None:
        if PAID_ENTRY_COPY_FIELD in result:
            return None
        result[PAID_ENTRY_COPY_FIELD] = spec.to_dict()
    return result


def _squad_count_schema(existing):
    result = deepcopy(dict(existing or {}))
    if 'field' in result:
        name = result.pop('field')
        result = {name: result}
    result['squad_count'] = {'type': 'integer', 'label': 'Squad payments',
        'minimum': 1, 'default': 1, 'required': True}
    return result


def expand_paid_entry_copy_costs(options, schema, *, response=None, hint=False):
    response = response or {}
    if PAID_ENTRY_COPY_FIELD not in schema:
        return [] if 'squad_count' in response else options
    spec = PaidEntryCopySpec.from_dict(schema[PAID_ENTRY_COPY_FIELD])
    count = 1
    if spec.keyword == 'squad' and not hint and 'squad_count' in response:
        count = response['squad_count']
        if type(count) is not int or count < 0:
            return []
        selected = response.get('cost_option')
        if count == 0:
            return options if selected in (None, '', *(row['id'] for row in options)) else []
        if selected and selected not in {row['id'] + '+squad:' + spec.instance_id for row in options}:
            return []
    elif spec.keyword != 'squad' and 'squad_count' in response:
        return []
    result = list(options)
    if spec.keyword == 'squad' and not hint and 'squad_count' in response and not response.get('cost_option'):
        result = []
    for option in options:
        paid = deepcopy(option)
        paid['id'] += '+' + spec.keyword + ':' + spec.instance_id
        paid['label'] = str(option.get('label') or 'Cast') + ' with ' + spec.keyword
        paid[PAID_ENTRY_COPY_FIELD] = validate_paid_entry_copy_fact(spec.payment_fact(count))
        if spec.keyword == 'squad':
            paid['choice_schema'] = _squad_count_schema(paid.get('choice_schema'))
        for symbol, amount in spec.requirements.items():
            paid['requirements'][symbol] += amount * count
        result.append(paid)
    return result


def paid_entry_copy_cast_context(option):
    value = option.get(PAID_ENTRY_COPY_FIELD)
    return {PAID_ENTRY_COPY_FIELD: validate_paid_entry_copy_fact(value)} if value is not None else {}
