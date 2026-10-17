from __future__ import annotations

"""Shared price/program correlation for closed, nonmodal text alternatives."""

from ..spell_text_variant_shapes import (
    fixed_variant_program_is_closed, variant_program_has_complete_admission, variant_spec,
)
from ...spell_text_variants import SpellTextVariantKind


def normal_variant_program(host, card, program, kind):
    if program is None or kind.boundary not in program.coverage:
        return None
    record = host.card_record(card)
    if record is None or record.layout != 'normal' or record.faces:
        return None
    normal = host.semantics.get(f'{record.oracle_id}:spell:front')
    if normal is None or kind.boundary not in normal.coverage:
        return None
    if (normal.oracle_id != record.oracle_id or not host.semantic_program_is_current_trusted(normal)
            or not variant_program_has_complete_admission(normal, kind)
            or not fixed_variant_program_is_closed(normal, kind)):
        raise ValueError('The text alternative requires its current complete normal program')
    return normal


def paid_variant_program(host, normal, spec, kind):
    paid = host.semantics.get(normal.key + ':' + kind.value)
    if (paid is None or paid.oracle_id != normal.oracle_id or paid.cost_schema != normal.cost_schema
            or paid.cost_schema[kind.value] != spec.to_dict()
            or not host.semantic_program_is_current_trusted(paid)
            or not variant_program_has_complete_admission(paid, kind)
            or not fixed_variant_program_is_closed(paid, kind)
            or paid.provenance.get('source_oracle_hash') != normal.provenance.get('source_oracle_hash')):
        raise ValueError('The text alternative requires its complete correlated derived program')
    return paid


def fixed_variant_cost_options(host, seat, card, program, *, kind, commander_tax,
        force_without_mana_cost, alternative_base, suppress_source_costs):
    try:
        normal = normal_variant_program(host, card, program, kind)
    except ValueError:
        return None
    if normal is None:
        record = host.card_record(card)
        return None if record is not None and kind.value in {str(k).casefold() for k in record.keywords} else []
    if force_without_mana_cost or alternative_base is not None or suppress_source_costs:
        return []
    try:
        spec = variant_spec(kind, normal.cost_schema[kind.value])
        paid = paid_variant_program(host, normal, spec, kind)
    except (KeyError, TypeError, ValueError):
        return None
    price = dict(spec.requirements)
    price['GENERIC'] += commander_tax
    return [{'id': kind.value, 'kind': 'alternate', 'label': 'Cast using ' + kind.value.capitalize(),
        'requirements': price, kind.value: spec.to_dict(),
        'target_schema': dict(paid.target_schema) if paid.target_schema is not None else {'groups': []}}]


def select_variant_program(host, card, program, option, kind):
    normal = normal_variant_program(host, card, program, kind)
    raw = option.get(kind.value)
    if raw is None:
        if normal is not None and program.key != normal.key:
            raise ValueError('An unpaid cast cannot select the derived text program')
        return program
    if normal is None or option.get('id') != kind.value:
        raise ValueError('The selected text alternative has no complete paired source')
    spec = variant_spec(kind, raw)
    if spec.to_dict() != normal.cost_schema[kind.value]:
        raise ValueError('The paid text choice no longer matches its source declaration')
    return paid_variant_program(host, normal, spec, kind)


def validate_variant_commit(host, card, program, option, kind):
    normal = normal_variant_program(host, card, program, kind)
    if normal is None and option.get(kind.value) is None:
        return
    expected = select_variant_program(host, card, normal, option, kind)
    if expected is None or expected.key != program.key:
        raise ValueError('The selected spell program does not match its paid text variant')


def select_variant_proposal_program(host, card, program, option, kind):
    from .model import CastProposalError
    try:
        return select_variant_program(host, card, program, option, kind)
    except ValueError as exc:
        raise CastProposalError(str(exc), reason=kind.value + '_variant_unavailable') from exc


def select_text_variant_proposal_program(host, card, program, option):
    for kind in SpellTextVariantKind:
        if option.get(kind.value) is not None or program is not None and kind.boundary in program.coverage:
            return select_variant_proposal_program(host, card, program, option, kind)
    return program
