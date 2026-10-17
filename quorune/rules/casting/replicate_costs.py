from __future__ import annotations

"""Current admitted Replicate cost and source-correlated immutable cast fact."""

from copy import deepcopy
from ...card_programs.admission import program_has_complete_card_program_admission
from ...replicate_model import (
    FixedManaReplicateSpec, REPLICATE_HANDLER, REPLICATE_FIELD, REPLICATE_COUNT_FIELD, validate_replicate_fact,
)
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
from .repeated_mana_costs import expand_repeated_mana_costs


def compiled_replicate_spec(host, card):
    record = host.card_record(card)
    if record is None or record.layout != 'normal' or record.faces or card.annotations.get('copy_overrides') is not None:
        return None
    registry = default_cast_cost_component_registry()
    found = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone='all', event='cast.cost'):
        descriptors = [row for row in program.handlers if row.get('handler_id') == REPLICATE_HANDLER]
        if (not host.semantic_program_is_current_trusted(program)
                or not program_has_complete_card_program_admission(program)):
            continue
        for descriptor in descriptors:
            found.extend(registry.lower(descriptor, None))
    return found[0] if len(found) == 1 else None


def with_replicate_cost_schema(host, card, schema, *, suppress_source_costs):
    result = deepcopy(dict(schema))
    spec = None if suppress_source_costs else compiled_replicate_spec(host, card)
    if spec is not None:
        if REPLICATE_FIELD in result:
            return None
        result[REPLICATE_FIELD] = spec.to_dict()
    return result


def expand_replicate_costs(options, schema, *, response=None, hint=False):
    return expand_repeated_mana_costs(options, schema, spec_type=FixedManaReplicateSpec,
        field=REPLICATE_FIELD, count_field=REPLICATE_COUNT_FIELD, label='Replicate', suffix_name='replicate',
        response=response, hint=hint)


def replicate_cast_context(option):
    value = option.get(REPLICATE_FIELD)
    return {REPLICATE_FIELD: validate_replicate_fact(value)} if value is not None else {}


def validate_replicate_commit(host, card, option):
    value = option.get(REPLICATE_FIELD)
    if value is None:
        return
    fact = validate_replicate_fact(value)
    spec = compiled_replicate_spec(host, card)
    if spec is None or fact != spec.payment_fact(fact['count']):
        raise ValueError('Replicate contract changed before commitment')
