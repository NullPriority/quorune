from __future__ import annotations

"""Read-only Fuse selection shared by advertised and accepted casting."""

from dataclasses import replace
from ..card_programs.admission import program_has_complete_card_program_admission
from ..semantic_runtime.fuse import FuseAbilityHandler
from .fuse_model import FuseSpec, FUSE_HANDLER, FUSE_ANNOTATION
from .fuse_shapes import closed_fused_program


def compiled_fuse_spec(host, card):
    record = host.card_record(card)
    if record is None or record.layout != 'split' or len(record.faces) != 2 or card.is_spell_copy or card.annotations.get('copy_overrides') is not None:
        return None
    result = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
            active_zone='all', event='characteristics.evaluate'):
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') == FUSE_HANDLER:
                spec = FuseAbilityHandler().validate(descriptor)
                fused = host.semantics.get(f'{record.oracle_id}:{spec.ability_id}')
                halves=[host.semantics.get(f'{record.oracle_id}:spell:{half.face_id}') for half in (spec.left,spec.right)]
                if (fused is not None and host.semantic_program_is_current_trusted(fused) and closed_fused_program(fused)
                        and all(p is not None and host.semantic_program_is_current_trusted(p) for p in halves)
                        and [p.to_dict() for p in halves]==fused.provenance['fuse_components']):
                    result.append(spec)
    return result[0] if len(result) == 1 else None


def fuse_source_cast_is_complete(host,card,program):
    if program is None or 'fuse_source' not in program.provenance:
        return True
    return card is not None and compiled_fuse_spec(host,card) is not None


def fuse_cast_view(host, card, record, cast_method):
    if cast_method != 'fuse':
        return card, record
    from .casting.model import CastProposalError
    spec = compiled_fuse_spec(host, card)
    if card.zone != 'hand' or spec is None:
        raise CastProposalError('Fuse requires a complete trusted split card in hand', reason='fuse_contract_unavailable')
    values = spec.characteristic_values()
    view = replace(card, active_face=None, annotations={**card.annotations,
        FUSE_ANNOTATION: spec.to_dict(), 'prospective_cast_fuse': True})
    return view, replace(record, name=values['name'], mana_cost=values['mana_cost'],
        mana_value=values['mana_value'], type_line=values['type_line'], colors=tuple(values['colors']))


def fuse_characteristic_values(card):
    raw = card.annotations.get(FUSE_ANNOTATION)
    if raw is None or (card.zone != 'stack' and card.annotations.get('prospective_cast_fuse') is not True):
        return {}
    return FuseSpec.from_dict(raw).characteristic_values()


def commit_fuse_cast(card, raw):
    if raw is not None:
        card.annotations[FUSE_ANNOTATION] = FuseSpec.from_dict(raw).to_dict()


def revalidate_fuse_cast(host, card, details):
    raw = details.get('fuse')
    if details.get('cast_method') != 'fuse':
        if raw is not None:
            raise ValueError('Ordinary casting cannot carry Fuse characteristics')
        return
    if card.zone != 'hand' or FuseSpec.from_dict(raw) != compiled_fuse_spec(host, card):
        raise ValueError('Fuse source or composition changed before commit')
