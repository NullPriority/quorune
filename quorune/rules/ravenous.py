"""Ravenous reads the spell that became an entry, using existing zone ownership."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Mapping
from ..ravenous_model import EntryXInformation,ENTRY_X_FIELD,ENTRY_X_CONDITION,validate_ravenous_condition

_RESOLVING=ContextVar('resolving_ravenous_entry_x',default=None)


def ravenous_reader_present(host):
    from ..entry_x_amount_model import entry_x_amount_dependencies
    return any((isinstance(program.event_condition,Mapping) and program.event_condition.get('field')==ENTRY_X_CONDITION
        or entry_x_amount_dependencies(program.effects)) and host.semantic_program_is_current_trusted(program)
        for program in host.semantics.programs_for_event('permanent.enter.self'))


@contextmanager
def resolving_ravenous_entry(host,item):
    if item.x_value is not None and (type(item.x_value) is not int or item.x_value<0):raise ValueError('Resolving spell X must be an exact nonnegative integer')
    token=_RESOLVING.set((host,item.card_object_id,item.x_value) if item.kind in {'spell','spell_copy'} else None)
    try:yield
    finally:_RESOLVING.reset(token)


def ravenous_entry_information(host,card,departure):
    if card.zone!='battlefield' or not ravenous_reader_present(host):return None
    current=_RESOLVING.get();amount=0
    if current is not None and current[0] is host and current[1]==card.object_id and departure.origin=='stack':amount=current[2] or 0
    return EntryXInformation(card.object_id,card.logical_object_id,amount).to_dict()


def ravenous_condition_value(condition,source,context):
    validate_ravenous_condition(condition)
    info=EntryXInformation.from_dict(context.get(ENTRY_X_FIELD))
    if info.object_id!=source.object_id:return False
    identity=context.get('card_object_identity')
    if identity is not None and identity!=info.logical_object_id:raise ValueError('Ravenous entry condition changed its event incarnation')
    return info.amount>=5
