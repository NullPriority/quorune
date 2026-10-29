"""Capture and preserve referenced spell information for the canonical copy owner."""

from dataclasses import replace
from collections.abc import Mapping

from ..event_spell_copy_model import (EventSpellCopySource,EVENT_SPELL_COPY_CONTEXT,EVENT_SPELL_COPY_MECHANIC,EVENT_SPELL_COPY_CAPABILITY,event_spell_copy_effect)
from ..replacement.immutable import thaw_value


def capture_event_spell_copy_source(host,item,*,departed=False):
    card=host.state.cards.get(item.card_object_id or '')
    if item.kind not in {'spell','spell_copy'} or card is None or card.zone!='stack':raise ValueError('Referenced copy source is not a current spell')
    program=host.semantics.get(item.semantic_key)
    from ..stack_resolution import trusted_generic_empty_resolution
    if (program is not None and not host.semantic_program_is_current_trusted(program)
            or program is None and trusted_generic_empty_resolution(host,item,None) is None):
        raise ValueError('Referenced spell has no current trusted resolution program')
    raw=item.to_dict();context=dict(raw['context'])
    for key in ('semantic_frame','currently_resolving','declared_scalar_amounts','targets_revalidated','target_groups_current'):
        context.pop(key,None)
    raw['context']=context
    return EventSpellCopySource(raw,host._copyable_characteristics(card),host._stack_target_schema(item,program),card.logical_object_id,departed)


def event_spell_copy_trigger_context(host,program,event,context):
    if not event_spell_copy_runtime_dependencies(program):return {}
    if event!='spell.cast':raise ValueError('Event spell copy requires a cast occurrence')
    target=next((i for i in host.state.stack if i.ref==context.get('stack')),None)
    if target is None:raise ValueError('Event spell-copy occurrence lost its exact stack spell')
    source=capture_event_spell_copy_source(host,target)
    if source.logical_object_id!=context.get('logical_object_id'):raise ValueError('Event spell-copy cast incarnation disagrees')
    return {EVENT_SPELL_COPY_CONTEXT:source.to_dict()}


def resolve_event_spell_copy_source(host,effect):
    if 'copy_source' not in effect:return None
    raw=effect.get('copy_source')
    source=EventSpellCopySource.from_dict(raw);item=source.item()
    if effect.get('stack')!=item.ref:raise ValueError('Event spell-copy source reference changed')
    live=next((i for i in host.state.stack if i.ref==item.ref),None)
    if live is not None:
        current=capture_event_spell_copy_source(host,live)
        if current.logical_object_id!=source.logical_object_id:raise ValueError('Another spell replaced the referenced incarnation')
        return current
    if source.departed:return source
    for pending in host.state.stack:
        raw=pending.context.get(EVENT_SPELL_COPY_CONTEXT)
        if raw is None:continue
        known=EventSpellCopySource.from_dict(raw)
        if known.item().ref==item.ref and known.logical_object_id==source.logical_object_id and known.departed:return known
    raise ValueError('Referenced spell departure information is unavailable')


def pin_event_spell_copy_departure(host,target):
    selected=[]
    for pending in host.state.stack:
        raw=pending.context.get(EVENT_SPELL_COPY_CONTEXT)
        if raw is None:continue
        source=EventSpellCopySource.from_dict(raw)
        if source.item().ref==target.ref:selected.append((pending,source))
    batches=[]
    for index,batch in enumerate(getattr(host.state,'pending_trigger_batches',())):
        matching=set()
        for group in batch.groups:
            for pending in group.items:
                raw=pending.payload.get('context',{}).get(EVENT_SPELL_COPY_CONTEXT)
                if raw is None:continue
                source=EventSpellCopySource.from_dict(raw)
                if source.item().ref==target.ref:
                    matching.add(pending.ref);selected.append((None,source))
        if matching:batches.append((index,batch,matching))
    if not selected:return
    latest=capture_event_spell_copy_source(host,target,departed=True)
    for pending,source in selected:
        if source.logical_object_id!=latest.logical_object_id:raise ValueError('Referenced spell-copy departure incarnation changed')
    def pin_occurrence(pending):
        from ..trigger_batches import PendingTriggerItem
        payload=pending.to_dict();payload['context'][EVENT_SPELL_COPY_CONTEXT]=latest.to_dict()
        return PendingTriggerItem.from_dict(payload)
    replacements=[(index,replace(batch,groups=tuple(replace(group,items=tuple(
        pin_occurrence(pending) if pending.ref in matching else pending for pending in group.items
    )) for group in batch.groups))) for index,batch,matching in batches]
    for pending,_source in selected:
        if pending is not None:pending.context[EVENT_SPELL_COPY_CONTEXT]=latest.to_dict()
    from ..trigger_processing import replace_pending_trigger_batch
    for index,batch in replacements:replace_pending_trigger_batch(host,index,batch)


def event_spell_copy_runtime_dependencies(program):
    marked=EVENT_SPELL_COPY_MECHANIC in program.coverage
    def contains(value):
        if isinstance(value,Mapping):
            if value.get('op')=='copy_stack_item' and 'copy_source' in value:
                if not any(dict(value)==event_spell_copy_effect(retain_targets_only=retain) for retain in (False,True)):raise ValueError('Event spell-copy instruction has a closed source reference')
                return True
            return any(contains(v) for v in value.values())
        return any(contains(v) for v in value) if isinstance(value,(list,tuple)) else False
    if not contains(program.effects):
        if marked:raise ValueError('Event spell-copy marker lacks a represented instruction')
        return ()
    if program.event!='spell.cast':raise ValueError('Event spell-copy instruction requires its normalized cast event')
    return (EVENT_SPELL_COPY_CAPABILITY,)
