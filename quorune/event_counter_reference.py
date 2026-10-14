from __future__ import annotations

"""Sealed public incarnation for one counter-result occurrence object."""

from collections.abc import Mapping

EVENT_COUNTER_OBJECT='$event.counter_object'
EVENT_COUNTER_CONTEXT='event_counter_recipient'


def event_counter_recipient_context(program,event,context):
    def contains(value):
        if isinstance(value,Mapping):return any(contains(v) for v in value.values())
        if isinstance(value,(list,tuple)):return any(contains(v) for v in value)
        return value==EVENT_COUNTER_OBJECT
    if not contains(program.effects):return {}
    reference=context.get('card');identity=None
    if event=='damage.dealt':identity=context.get('source_logical_object_id')
    elif event=='creature.attacks':
        from .attack_transition_model import AttackTransitionEvent
        transition=AttackTransitionEvent.from_dict(context.get('attack_transition'))
        participant=next((p for p in transition.participants if p.reference==reference),None)
        identity=participant.logical_object_id if participant is not None else None
    if type(reference) is not str or not reference or type(identity) is not str or not identity:
        raise ValueError('Counter event recipient has no sealed public incarnation')
    return {EVENT_COUNTER_CONTEXT:{'reference':reference,'logical_object_id':identity}}


def _resolve_public_incarnation(host,value):
    if (not isinstance(value,Mapping) or set(value)!={'reference','logical_object_id'}
            or any(type(value[k]) is not str or not value[k] for k in value)):
        raise ValueError('Counter event recipient snapshot is missing or malformed')
    card=next((card for card in host.state.cards.values() if card.ref==value['reference']),None)
    return card.ref if card is not None and card.zone=='battlefield' and not card.phased_out and card.logical_object_id==value['logical_object_id'] else None


def resolve_event_counter_recipient(host,item):
    return _resolve_public_incarnation(host,item.context.get(EVENT_COUNTER_CONTEXT))


def resolve_entry_event_counter_recipient(host,item):
    reference=item.context.get('card');identity=item.context.get('card_object_identity')
    if type(reference) is not str or type(identity) is not str or not identity:
        raise ValueError('Entry event object identity is unavailable')
    return _resolve_public_incarnation(host,{'reference':reference,'logical_object_id':identity})
