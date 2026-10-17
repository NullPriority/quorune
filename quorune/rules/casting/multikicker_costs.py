from __future__ import annotations

"""Admitted Multikicker price expansion and source-pinned cast facts."""

from copy import deepcopy

from ...card_programs.admission import program_has_complete_card_program_admission
from ...multikicker_model import (
    FixedManaMultikickerSpec, MULTIKICKER_FIELD, MULTIKICKER_COUNT_FIELD,
    MULTIKICKER_COST_HANDLER, validate_multikicker_fact,
)
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry


def compiled_multikicker_spec(host, card):
    record = host.card_record(card)
    if record is None or record.layout != 'normal' or record.faces or card.annotations.get('copy_overrides') is not None:
        return None
    registry = default_cast_cost_component_registry()
    found = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone='all', event='cast.cost'):
        descriptors = [row for row in program.handlers if row.get('handler_id') == MULTIKICKER_COST_HANDLER]
        if not descriptors:
            continue
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):
            continue
        for descriptor in descriptors:
            found.extend(registry.lower(descriptor, None))
    return found[0] if len(found) == 1 else None


def with_multikicker_cost_schema(host, card, schema, *, suppress_source_costs):
    result = deepcopy(dict(schema))
    spec = None if suppress_source_costs else compiled_multikicker_spec(host, card)
    if spec is not None:
        if MULTIKICKER_FIELD in result:
            return None
        result[MULTIKICKER_FIELD] = spec.to_dict()
    return result


def expand_multikicker_costs(options, schema, *, response=None, hint=False):
    from .repeated_mana_costs import expand_repeated_mana_costs
    return expand_repeated_mana_costs(options, schema, spec_type=FixedManaMultikickerSpec,
        field=MULTIKICKER_FIELD, count_field=MULTIKICKER_COUNT_FIELD, label='Multikicker', suffix_name='multikicker',
        response=response, hint=hint)


def multikicker_cast_context(option):
    value = option.get(MULTIKICKER_FIELD)
    return {MULTIKICKER_FIELD: validate_multikicker_fact(value)} if value is not None else {}


def validate_multikicker_commit(host, card, option):
    value = option.get(MULTIKICKER_FIELD)
    if value is None:
        return
    fact = validate_multikicker_fact(value)
    spec = compiled_multikicker_spec(host, card)
    if spec is None or fact != spec.payment_fact(fact['count']):
        raise ValueError('Multikicker contract changed before commitment')
