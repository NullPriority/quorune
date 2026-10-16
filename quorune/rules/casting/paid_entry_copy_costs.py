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


def expand_paid_entry_copy_costs(options, schema):
    if PAID_ENTRY_COPY_FIELD not in schema:
        return options
    spec = PaidEntryCopySpec.from_dict(schema[PAID_ENTRY_COPY_FIELD])
    result = list(options)
    for option in options:
        paid = deepcopy(option)
        paid['id'] += '+offspring:' + spec.instance_id
        paid['label'] = str(option.get('label') or 'Cast') + ' with offspring'
        paid[PAID_ENTRY_COPY_FIELD] = spec.payment_fact()
        for symbol, amount in spec.requirements.items():
            paid['requirements'][symbol] += amount
        result.append(paid)
    return result


def paid_entry_copy_cast_context(option):
    value = option.get(PAID_ENTRY_COPY_FIELD)
    return {PAID_ENTRY_COPY_FIELD: validate_paid_entry_copy_fact(value)} if value is not None else {}
