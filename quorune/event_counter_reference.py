from __future__ import annotations

"""Sealed public incarnation for one counter-result occurrence object."""

from collections.abc import Mapping
from .damage_recipient_model import (DAMAGE_PERMANENT_OBJECT,DAMAGE_PERMANENT_CONTEXT,
    uses_damaged_permanent,capture_damaged_permanent)

EVENT_COUNTER_OBJECT='$event.counter_object'
EVENT_ENTRY_OBJECT='$event.entry_object'
EVENT_COUNTER_CONTEXT='event_counter_recipient'
BLOCK_COUNTERPART_OBJECT = '$event.block_counterpart'
BLOCK_COUNTERPART_CONTEXT = 'block_counterpart_recipient'


def event_counter_recipient_context(program,event,context):
    if uses_damaged_permanent(program.effects):return capture_damaged_permanent(event,context)
    def uses_pair(value):
        if isinstance(value, Mapping):return any(uses_pair(child) for child in value.values())
        if isinstance(value, (list, tuple)):return any(uses_pair(child) for child in value)
        return value == BLOCK_COUNTERPART_OBJECT
    if uses_pair(program.effects):
        from .block_transitions import BlockTransitionEvent
        transition = BlockTransitionEvent.from_dict(context.get('block_transition'))
        reference = context.get('card')
        counterpart = context.get('blocked_attacker') if event == 'creature.blocks' else context.get('blocking_creature') if event == 'creature.becomes_blocked_by' else None
        participants = {participant.reference: participant for participant in transition.participants}
        actor, other = participants.get(reference), participants.get(counterpart)
        if actor is None or other is None:
            raise ValueError('Block counterpart requires two sealed transition participants')
        attacker, blocker = (other, actor) if event == 'creature.blocks' else (actor, other)
        if not any(row.attacker_object_id == attacker.object_id and row.blocker_object_id == blocker.object_id
                   for row in transition.assignments):
            raise ValueError('Block counterpart is not the selected blocking pair')
        return {BLOCK_COUNTERPART_CONTEXT: {'reference': other.reference, 'logical_object_id': other.logical_object_id}}
    def contains(value):
        if isinstance(value,Mapping):return any(contains(v) for v in value.values())
        if isinstance(value,(list,tuple)):return any(contains(v) for v in value)
        return value in (EVENT_COUNTER_OBJECT,'$event.permanent_object')
    if not contains(program.effects):return {}
    reference=context.get('card');identity=None
    if event=='damage.dealt':identity=context.get('source_logical_object_id')
    elif event in {'damage.received','combat.damage.received'}:
        if context.get('target_kind')!='permanent':raise ValueError('Received event object must be a permanent')
        identity=context.get('target_logical_object_id')
    elif event in {'permanent.tap','permanent.untap'}:identity=context.get('card_object_identity')
    elif event=='creature.attacks':
        from .attack_transition_model import AttackTransitionEvent
        transition=AttackTransitionEvent.from_dict(context.get('attack_transition'))
        participant=next((p for p in transition.participants if p.reference==reference),None)
        identity=participant.logical_object_id if participant is not None else None
    elif event == 'creature.becomes_blocked':
        from .block_transitions import BlockTransitionEvent
        transition = BlockTransitionEvent.from_dict(context.get('block_transition'))
        attacker_ids = {row.attacker_object_id for row in transition.assignments}
        participant = next((p for p in transition.participants if p.reference == reference
                            and p.object_id in attacker_ids), None)
        identity = participant.logical_object_id if participant is not None else None
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


def resolve_block_counterpart_recipient(host, item):
    return _resolve_public_incarnation(host, item.context.get(BLOCK_COUNTERPART_CONTEXT))


def resolve_sealed_event_object_value(host, item, value):
    if value==DAMAGE_PERMANENT_OBJECT:
        return True,_resolve_public_incarnation(host,item.context.get(DAMAGE_PERMANENT_CONTEXT))
    if value == BLOCK_COUNTERPART_OBJECT:
        return True, resolve_block_counterpart_recipient(host, item)
    if value in {EVENT_ENTRY_OBJECT, EVENT_COUNTER_OBJECT, '$event.permanent_object'}:
        resolver = resolve_entry_event_counter_recipient if value == EVENT_ENTRY_OBJECT else resolve_event_counter_recipient
        return True, resolver(host, item)
    return False, None


def resolve_entry_event_counter_recipient(host,item):
    reference=item.context.get('card');identity=item.context.get('card_object_identity')
    if type(reference) is not str or type(identity) is not str or not identity:
        raise ValueError('Entry event object identity is unavailable')
    return _resolve_public_incarnation(host,{'reference':reference,'logical_object_id':identity})
