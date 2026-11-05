"""Capture and resolve through the existing delayed-trigger and effect owners."""
from collections.abc import Mapping
from .delayed_object_model import (SCHEDULE_OBJECT_ACTION,RESOLVE_OBJECT_ACTION,OBJECT_TIMINGS,
    validate_delayed_object_instruction)
from .errors import GameRuleError
from .event_counter_reference import BLOCK_COUNTERPART_CONTEXT
from .damage_recipient_model import DAMAGE_PERMANENT_OBJECT,DAMAGE_PERMANENT_CONTEXT
from .target_source import TARGETING_SOURCE_CONTEXT,TargetingSourceBinding
from .trigger_processing import schedule_delayed_trigger


def bind_delayed_object_instruction(host,effect,item):
    validate_delayed_object_instruction(effect,compiled=True)
    if effect['object']=='$event.block_counterpart':obj=item.context.get(BLOCK_COUNTERPART_CONTEXT)
    elif effect['object']==DAMAGE_PERMANENT_OBJECT:obj=item.context.get(DAMAGE_PERMANENT_CONTEXT)
    else:
        reference=host._stack_source_ref(item)
        identity=item.context.get('source_logical_object_id')
        raw=item.context.get(TARGETING_SOURCE_CONTEXT)
        if reference is None and isinstance(raw,Mapping):reference=TargetingSourceBinding.from_dict(raw).object_ref
        obj={'reference':reference,'logical_object_id':identity}
    result={**effect,'object':dict(obj) if isinstance(obj,Mapping) else obj}
    if effect['schema_version']==2:
        reference=host._stack_source_ref(item)
        raw=item.context.get(TARGETING_SOURCE_CONTEXT)
        if reference is None and isinstance(raw,Mapping):reference=TargetingSourceBinding.from_dict(raw).object_ref
        result['source']=reference
    validate_delayed_object_instruction(result)
    return result


def apply_delayed_object_action(host,effect,*,actor,operation,reason):
    instruction={key:value for key,value in effect.items() if key not in {'reason','_runtime_source'}}
    try:validate_delayed_object_instruction(instruction)
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    if instruction['op']!=operation:raise GameRuleError('Delayed action operation changed')
    if operation==SCHEDULE_OBJECT_ACTION:
        source=effect.get('_runtime_source')
        if not isinstance(source,Mapping):raise GameRuleError('Delayed object scheduling requires its creating stack source')
        future={key:value for key,value in instruction.items() if key not in {'op','timing'}}
        future.update(op=RESOLVE_OBJECT_ACTION,object=dict(instruction['object']))
        label=('Delayed counter placement' if instruction['action']=='place_counters' else 'Delayed counter removal') if instruction['schema_version']==2 else 'Delayed '+instruction['action']
        return schedule_delayed_trigger(host,controller=actor,label=label,
            event_kind='step.begin',condition=OBJECT_TIMINGS[instruction['timing']],
            stack_template={'label':label,'context':{'dynamic_effects':[future]}},
            source_object_id=source['object_id'],source_logical_object_id=source['logical_object_id'],once=True).ref
    obj=instruction['object'];card=next((c for c in host.state.cards.values() if c.ref==obj['reference']),None)
    if (card is None or card.zone!='battlefield' or card.phased_out
            or card.logical_object_id!=obj['logical_object_id']
            or instruction['action']=='sacrifice' and card.controller!=actor):return None
    action={'op':instruction['action'],'card':card.ref,'reason':reason}
    if instruction['schema_version']==2:action.update({key:instruction[key] for key in ('counter','amount','source')})
    return host.apply_effect(action,actor=actor)
