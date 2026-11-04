from __future__ import annotations

"""Read a real stack spell's cast receipt from canonical current-turn history.

This query does not assert that a later entry is the spell resolving. The
resolution/zone owner must establish that relationship before attaching it.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Mapping

from ..source_entry_cast_information import (CastSpellReceipt,EntryCastInformation,
    SOURCE_ENTRY_CAST_CONDITION,SOURCE_ENTRY_CAST_INFORMATION_FIELD)
from ..continuous_effect_model import ContinuousObjectIdentity
from ..turn_history import current_turn_history_events

_RESOLVING_ENTRY:ContextVar[object]=ContextVar('resolving_entry_cast_information',default=None)


def source_entry_cast_reader_present(host):
    registry=getattr(host,'semantics',None)
    programs=getattr(registry,'programs_for_event',None)
    return callable(programs) and any(
        isinstance(getattr(program,'event_condition',None),Mapping) and program.event_condition.get('field')==SOURCE_ENTRY_CAST_CONDITION
        and host.semantic_program_is_current_trusted(program)
        for program in programs('permanent.enter.self'))


@contextmanager
def resolving_entry_cast_information(host,item):
    if not source_entry_cast_reader_present(host):
        yield;return
    card=host.state.cards.get(item.card_object_id or '')
    receipt=capture_stack_cast_receipt(host,card) if card is not None and item.kind=='spell' else None
    token=_RESOLVING_ENTRY.set((host,item.card_object_id,receipt))
    try:yield
    finally:_RESOLVING_ENTRY.reset(token)


def information_for_committed_entry(host,card,departure):
    if card.zone!='battlefield' or not source_entry_cast_reader_present(host):return None
    current=_RESOLVING_ENTRY.get();receipt=None
    if current is not None and current[0] is host and current[1]==card.object_id and departure.origin=='stack':
        receipt=current[2]
        if receipt is not None and receipt.stack_logical_object_id!=departure.logical_object_id:
            raise ValueError('The resolving entry changed its original stack incarnation')
    return EntryCastInformation(ContinuousObjectIdentity(card.object_id,card.logical_object_id),receipt).to_dict()


def source_entry_cast_value(host,context,*,controller,source,origin=None):
    info=EntryCastInformation.from_dict(context.get(SOURCE_ENTRY_CAST_INFORMATION_FIELD))
    if controller not in host.state.players or info.cast is not None and (
            info.cast.caster not in host.state.players or info.cast.origin_owner not in host.state.players):
        raise ValueError('Source-entry cast information names an unknown player')
    return info.was_cast_by(controller,source=source,origin=origin)


def capture_stack_cast_receipt(host,card):
    if card.zone!='stack':return None
    items=tuple(item for item in host.state.stack
        if item.card_object_id==card.object_id and item.kind in {'spell','spell_copy'})
    if len(items)!=1:raise ValueError('Cast information requires one exact current stack spell')
    item=items[0]
    if item.kind=='spell_copy':return None
    records=tuple(event for event in current_turn_history_events(host.state.turn_history,
        turn_sequence=host.state.turn_sequence,kind='spell_cast')
        if event.object_incarnation==card.logical_object_id)
    if len(records)!=1:raise ValueError('The actual caster is unavailable or ambiguous in cast history')
    event=records[0];origin=item.context.get('cast_origin')
    if event.origin is not None and event.origin!=origin:
        raise ValueError('Cast history and stack origin disagree')
    if event.actor not in host.state.players or card.owner not in host.state.players:
        raise ValueError('A cast receipt cannot name an unknown player')
    return CastSpellReceipt(event.actor,origin,card.owner,item.ref,card.logical_object_id)
