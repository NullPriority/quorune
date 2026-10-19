from __future__ import annotations

"""Prepared designations and linked copies through the canonical object owners."""

from dataclasses import replace
from .preparation_model import (PrepareSpellSpec,PreparedDesignation,PREPARED_FIELD,PREPARE_SPELL_FIELD,
    PREPARE_COPY_FIELD,PREPARATION_HANDLER)
from .errors import GameRuleError


def compiled_prepare_spell(host,card):
    """Read copiable alternatives independently from current ability presence."""
    from .entry_copy import copiable_oracle_id
    from .card_programs.admission import program_has_complete_card_program_admission
    from .semantic_runtime.preparation import PreparationEntryHandler
    values=card.annotations.get('copy_overrides',{})
    if card.face_down or values.get('is_prepare_spell') is True:return None
    if PREPARE_SPELL_FIELD in values:return PrepareSpellSpec.from_dict(values[PREPARE_SPELL_FIELD])
    if values:return None
    record=host.card_record(card)
    if record is None or record.layout!='prepare' or len(record.faces)!=2:return None
    specs=[];handler=PreparationEntryHandler()
    for program in host.semantics.runtime_handler_programs_for_oracle(copiable_oracle_id(card),active_zone='all',event='zone.change'):
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):continue
        for value in program.handlers:
            if value.get('handler_id')==PREPARATION_HANDLER:specs.append(handler.validate(value))
    return specs[0] if len(specs)==1 else None


def prepared_designation(card):
    raw=card.annotations.get(PREPARED_FIELD)
    if raw is None:return None
    try:value=PreparedDesignation.from_dict(raw)
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    if (value.source_id!=card.object_id or value.source_ref!=card.ref or value.source_identity!=card.logical_object_id):
        raise GameRuleError('Prepared designation lost its original permanent incarnation')
    return value


def _create_prepare_spell_copy(host,source,designation):
    from .entry_copy import COPIABLE_ORACLE_FIELD,COPIABLE_FACE_FIELD
    spell=designation.spell
    values={'name':spell.face_id,'mana_cost':spell.mana_cost,'mana_value':spell.mana_value,'type_line':spell.type_line,
        'colors':list(spell.colors),'power':None,'toughness':None,'loyalty':None,'defense':None,'keywords':[],
        COPIABLE_ORACLE_FIELD:spell.oracle_id,COPIABLE_FACE_FIELD:spell.face_id,'is_prepare_spell':True}
    copy=host._create_copy_object(controller=source.controller,source=source,characteristics=values,object_kind='card_copy',zone='exile')
    copy.active_face=spell.face_id
    binding=replace(designation,copy_id=copy.object_id)
    copy.annotations[PREPARE_COPY_FIELD]=binding.to_dict()
    source.annotations[PREPARED_FIELD]=binding.to_dict()
    host._log(source.controller,'permanent.prepared',f'{source.ref} became prepared.',
        {'source':source.ref,'copy':copy.ref,'prepare_spell':spell.face_id},changed_objects=[source.object_id,copy.object_id])
    return copy


def become_prepared(host,source,*,spell=None):
    if source.zone!='battlefield' or source.phased_out:raise GameRuleError('Only a present battlefield permanent can become prepared')
    if prepared_designation(source) is not None:return False
    specification=spell or compiled_prepare_spell(host,source)
    if specification is None:return False
    designation=PreparedDesignation(source.object_id,source.ref,source.logical_object_id,specification)
    _create_prepare_spell_copy(host,source,designation)
    return True


def prepare_copy_link(host,copy):
    raw=copy.annotations.get(PREPARE_COPY_FIELD)
    if raw is None:return None
    try:binding=PreparedDesignation.from_dict(raw)
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    source=host.state.cards.get(binding.source_id)
    if (source is None or source.ref!=binding.source_ref or source.logical_object_id!=binding.source_identity
            or source.zone!='battlefield' or source.phased_out or source.controller not in host.active_seats):return None
    designation=prepared_designation(source)
    if designation is None or designation.copy_id!=copy.object_id or designation!=binding:return None
    return source,designation


def preparation_copy_retained(host,copy):
    return copy.is_card_copy and copy.zone=='exile' and prepare_copy_link(host,copy) is not None


def preparation_cast_permission(host,seat,copy):
    linked=prepare_copy_link(host,copy) if copy.is_card_copy and copy.zone=='exile' else None
    return linked is not None and linked[0].controller==seat


def synchronize_preparation_copies(host):
    """A phased-out permanent keeps its designation; phasing in creates a new copy."""
    for source in tuple(host.state.cards.values()):
        if source.zone!='battlefield' or PREPARED_FIELD not in source.annotations:continue
        designation=prepared_designation(source)
        if source.phased_out:
            if designation.copy_id is not None:source.annotations[PREPARED_FIELD]=replace(designation,copy_id=None).to_dict()
        elif designation.copy_id is None:_create_prepare_spell_copy(host,source,designation)


def consume_prepared_cast(host,copy):
    if PREPARE_COPY_FIELD not in copy.annotations:return
    linked=prepare_copy_link(host,copy)
    if linked is None:raise GameRuleError('Accepted prepare spell cast lost its prepared permanent')
    source,_=linked
    source.annotations.pop(PREPARED_FIELD)
    host._log(copy.controller,'permanent.unprepared',f'{source.ref} became unprepared.',
        {'source':source.ref,'spell':copy.ref},changed_objects=[source.object_id,copy.object_id])


def preparation_cast_face(card,record,requested=None):
    raw=card.annotations.get(PREPARE_COPY_FIELD)
    if raw is not None:
        spell=PrepareSpellSpec.from_dict(raw['spell'])
        if requested is not None and requested.casefold()!=spell.face_id.casefold():raise GameRuleError('Prepare copy has only its inset spell characteristics')
        return spell.face_id
    if record.layout=='prepare' and requested is not None and requested.casefold()!=str(record.faces[0].get('name')).casefold():
        raise GameRuleError('A physical preparation card cannot be cast as its inset spell')
    return requested


def canonical_preparation_name(record,submitted):
    """Preserve a preparation card's individually nameable characteristic set."""
    if record.layout=='prepare':
        from .util import normalize_card_name
        normalized=normalize_card_name(submitted)
        for face in record.faces:
            if normalize_card_name(str(face.get('name') or ''))==normalized:return str(face['name'])
        return None
    return record.name
