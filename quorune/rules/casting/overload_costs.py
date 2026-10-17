from __future__ import annotations

"""Correlate fixed Overload payment with its complete generated spell program."""

from ...overload_model import FixedManaOverloadSpec, OVERLOAD_FIELD, OVERLOAD_BOUNDARY
from ..overload_shapes import fixed_overload_program_is_closed, overload_program_has_complete_admission


def normal_overload_program(host, card, program):
    if program is None or OVERLOAD_BOUNDARY not in program.coverage:
        return None
    record = host.card_record(card)
    if record is None or record.layout != 'normal' or record.faces:
        return None
    normal = host.semantics.get(f'{record.oracle_id}:spell:front')
    if normal is None or OVERLOAD_BOUNDARY not in normal.coverage:
        return None
    if (normal.oracle_id != record.oracle_id or not host.semantic_program_is_current_trusted(normal)
            or not overload_program_has_complete_admission(normal)
            or not fixed_overload_program_is_closed(normal)):
        raise ValueError('Overload requires its current complete normal source program')
    return normal


def overload_cost_options(host, seat, card, program, *, commander_tax,
        force_without_mana_cost, alternative_base, suppress_source_costs):
    try:
        normal = normal_overload_program(host, card, program)
    except ValueError:
        return None
    if normal is None:
        record = host.card_record(card)
        return None if record is not None and 'overload' in {str(k).casefold() for k in record.keywords} else []
    if force_without_mana_cost or alternative_base is not None or suppress_source_costs:
        return []
    try:
        spec = FixedManaOverloadSpec.from_dict(normal.cost_schema[OVERLOAD_FIELD])
        _paid_overload_program(host, normal, spec)
    except (KeyError, TypeError, ValueError):
        return None
    price = dict(spec.requirements)
    price['GENERIC'] += commander_tax
    return [{'id': OVERLOAD_FIELD, 'kind': 'alternate', 'label': 'Cast using Overload',
        'requirements': price, OVERLOAD_FIELD: spec.to_dict(), 'target_schema': {'groups': []}}]


def _paid_overload_program(host, normal, spec):
    paid = host.semantics.get(normal.key + ':overload')
    if (paid is None or paid.oracle_id != normal.oracle_id or paid.cost_schema != normal.cost_schema
            or paid.cost_schema[OVERLOAD_FIELD] != spec.to_dict()
            or not host.semantic_program_is_current_trusted(paid)
            or not overload_program_has_complete_admission(paid)
            or not fixed_overload_program_is_closed(paid)
            or paid.provenance.get('source_oracle_hash') != normal.provenance.get('source_oracle_hash')):
        raise ValueError('Overload requires its current complete correlated target-free program')
    return paid


def select_overload_cast_program(host, card, program, option):
    normal = normal_overload_program(host, card, program)
    raw = option.get(OVERLOAD_FIELD)
    if raw is None:
        if normal is not None and program.key != normal.key:
            raise ValueError('An unpaid cast cannot select the overloaded program')
        return program
    if normal is None or option.get('id') != OVERLOAD_FIELD:
        raise ValueError('The selected Overload cost has no complete paired source')
    spec = FixedManaOverloadSpec.from_dict(raw)
    if spec.to_dict() != normal.cost_schema[OVERLOAD_FIELD]:
        raise ValueError('Overload payment no longer matches the source declaration')
    return _paid_overload_program(host, normal, spec)


def validate_overload_commit(host, card, program, option):
    normal = normal_overload_program(host, card, program)
    if normal is None and option.get(OVERLOAD_FIELD) is None:
        return
    expected = select_overload_cast_program(host, card, normal, option)
    if expected is None or expected.key != program.key:
        raise ValueError('The selected spell program does not match its paid Overload variant')


def select_overload_proposal_program(host, card, program, option):
    from .model import CastProposalError
    try:
        return select_overload_cast_program(host, card, program, option)
    except ValueError as exc:
        raise CastProposalError(str(exc), reason='overload_variant_unavailable') from exc
