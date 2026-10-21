from __future__ import annotations

"""Apply bounded additions, counter-order choices and subtype entry results."""

from dataclasses import replace
from .applicability import replacement_choice
from .immutable import thaw_value
from .model import ReplacementEffectError
from .operations import (AddAffectedObjectCounter,ApplyEntryCounterReplacement,GrantAffectedObjectSubtype,
    CreateAffectedObjectCounter,GrantAffectedObjectKeyword)


def add_entry_counters(event,payload,children,operation,*,effect_id):
    from .application import _create_affected_object_counter_event
    if event.kind!='zone.change' or event.affected_object is None or payload.get('destination')!='battlefield':
        raise ReplacementEffectError('Additional entry counters require battlefield entry')
    payload['additional_entry_counter_instruction']=True
    matching=[i for i,child in enumerate(children) if child.kind=='counter.place'
        and child.affected_object is not None and event.affected_object is not None
        and child.affected_object.object_id==event.affected_object.object_id
        and child.payload.get('follows_zone_destination') is True
        and child.payload.get('counter_name')==operation.counter_name]
    if not matching:
        _create_affected_object_counter_event(event,payload,children,operation,effect_id=effect_id)
        return
    if len(matching)>1:
        contributions=[children[i] for i in matching]
        if any(c.applied_effects or c.children or c.payload.get('placing_player')!=operation.placing_player for c in contributions):
            raise ReplacementEffectError('Entry counter contributions must combine before replacements')
        combined=thaw_value(contributions[0].payload)
        for field in ('amount','requested_amount'):
            amounts=[c.payload.get(field) for c in contributions]
            if any(type(v) is not int or v<0 for v in amounts):raise ReplacementEffectError('Entry counter contributions require nonnegative quantities')
            combined[field]=sum(amounts)
        children[matching[0]]=replace(contributions[0],payload=combined)
        for index in reversed(matching[1:]):del children[index]
        matching=matching[:1]
    index=matching[0];child=children[index]
    if child.payload.get('placing_player')!=operation.placing_player:
        raise ReplacementEffectError('Additional entry counter placing player changed')
    amount=child.payload.get('amount');requested=child.payload.get('requested_amount')
    if type(amount) is not int or type(requested) is not int or amount<0 or requested<0:
        raise ReplacementEffectError('Additional entry counters require nonnegative current quantities')
    children[index]=replace(child,payload={**thaw_value(child.payload),'amount':amount+operation.amount,
        'requested_amount':requested+operation.amount})


def apply_entry_counter_operation(event,payload,children,operation,*,effect_id):
    if isinstance(operation,ApplyEntryCounterReplacement):
        from .application import apply_replacement
        matching=[i for i,child in enumerate(children) if child.event_id==operation.event_id]
        if event.kind!='zone.change' or len(matching)!=1:
            raise ReplacementEffectError('Entry counter replacement subject is unavailable')
        index=matching[0];choice=replacement_choice(children[index],(operation.effect,))
        if choice is None:raise ReplacementEffectError('Entry counter replacement is no longer applicable')
        children[index]=apply_replacement(choice,(operation.effect,),operation.effect.effect_id)
    elif isinstance(operation,AddAffectedObjectCounter):
        add_entry_counters(event,payload,children,operation,effect_id=effect_id)
    elif isinstance(operation,GrantAffectedObjectSubtype):
        if event.kind!='zone.change' or payload.get('destination')!='battlefield':
            raise ReplacementEffectError('Entry subtype grants require battlefield entry')
        grants=payload.get('entry_subtype_grants',())
        if not isinstance(grants,(list,tuple)):raise ReplacementEffectError('Entry subtype grants must be an array')
        payload['entry_subtype_grants']=[*list(grants),{'effect_id':effect_id,'subtype':operation.subtype,'sequence':operation.sequence}]
        payload['object_types']=sorted({*payload.get('object_types',()),operation.subtype})
    elif isinstance(operation,CreateAffectedObjectCounter):
        from .application import _create_affected_object_counter_event
        _create_affected_object_counter_event(event,payload,children,operation,effect_id=effect_id)
    elif isinstance(operation,GrantAffectedObjectKeyword):
        from .application import _apply_affected_object_keyword
        _apply_affected_object_keyword(event,payload,operation,effect_id=effect_id)
    else:raise ReplacementEffectError('Unsupported additional entry operation')
