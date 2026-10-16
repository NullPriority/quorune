from __future__ import annotations

"""Replay-pinned remaining intents after an already committed choice intent."""

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime import intents as typed
from ..semantic_runtime.domain_intent import DomainEffectIntent
from ..semantic_runtime.exploit_intent import ExploitSacrificeIntent
from ..zone_trigger_events import ZoneTransitionKind
from .model import SemanticChoiceCompletion, SemanticChoiceContinuation, SemanticChoiceError


def _intent_types():
    return {cls.__name__:cls for cls in (
        typed.LifeChangeIntent, typed.ZoneMoveIntent, typed.MoveObjectsSimultaneouslyIntent,
        typed.DestroyPermanentIntent, typed.ExilePermanentIntent, typed.ReturnPermanentToOwnerHandIntent,
        typed.ReturnGraveyardCardToOwnerHandIntent,
        typed.RecordChoiceIntent, typed.ExploreCompletedIntent, typed.ConniveCompletedIntent,
        typed.SetPermanentTappedIntent, typed.SetPermanentsTappedIntent, typed.ProhibitNextUntapIntent, DomainEffectIntent, ExploitSacrificeIntent,
        typed.PlaceCountersIntent,typed.PlacePlayerCountersIntent,typed.PlaceCounterBatchIntent,typed.RemoveCountersIntent,
        typed.QueueReflexiveTriggerIntent,
    )}


def _value_types():
    from ..connive import ConnivingObject
    from ..semantic_runtime.intents import CounterPlacementAmount
    from ..entry_counter_model import EffectEntryCounter
    return {cls.__name__:cls for cls in (ConnivingObject,CounterPlacementAmount,EffectEntryCounter)}


def _data(value):
    if isinstance(value,FrozenMap):return thaw_value(value)
    if isinstance(value,Enum):return value.value
    if is_dataclass(value):
        if _value_types().get(type(value).__name__) is not type(value):
            raise SemanticChoiceError('Completed choice contains an unknown typed value')
        return {'value_kind':type(value).__name__,'fields':{field.name:_data(getattr(value,field.name)) for field in fields(value)}}
    if isinstance(value,Mapping):return {key:_data(child) for key,child in value.items()}
    if isinstance(value,(list,tuple)):return [_data(child) for child in value]
    if value is None or type(value) in (str,int,float,bool):return value
    raise SemanticChoiceError('Completed choice contains an unsupported data value')


def _restore(value):
    if isinstance(value,Mapping) and set(value)=={'value_kind','fields'}:
        cls=_value_types().get(value['value_kind']);data=value['fields']
        if cls is None or not isinstance(data,Mapping) or set(data)!={field.name for field in fields(cls)}:
            raise SemanticChoiceError('Completed choice typed value is malformed')
        return cls(**{key:_restore(child) for key,child in data.items()})
    if isinstance(value,Mapping):return {key:_restore(child) for key,child in value.items()}
    if isinstance(value,(list,tuple)):return tuple(_restore(child) for child in value)
    return value


def encode_remaining_completion(continuation, actor, response, completion, next_index):
    intents=[]
    for intent in completion.intents[next_index:]:
        if not is_dataclass(intent) or _intent_types().get(type(intent).__name__) is not type(intent):
            raise SemanticChoiceError('Completed choice has an unrepresented remaining intent')
        intents.append({'kind':type(intent).__name__,'fields':{field.name:_data(getattr(intent,field.name)) for field in fields(intent)}})
    return {'schema_version':1,'kind':'completed_semantic_choice','continuation':continuation.to_dict(),
        'actor':actor,'response':_data(response),'intents':intents,
        'prepend_effects':_data(completion.prepend_effects),'repeat_effect':_data(completion.repeat_effect)}


def encode_preparation_after_commit(continuation,actor,preparation,intents,results):
    if preparation.auto_continue is not None and preparation.auto_continue.amount_from_result is not None:
        binding=preparation.auto_continue.amount_from_result
        if binding.intent_index not in results:
            raise SemanticChoiceError('Conditional preparation requires its remaining result-bound intents')
    if preparation.auto_continue is not None:
        completion=SemanticChoiceCompletion(intents=tuple(intents),
            prepend_effects=preparation.auto_continue.resolved_effects(results))
        return encode_remaining_completion(continuation,actor,{},completion,0)
    if intents:
        raise SemanticChoiceError('Request preparation has unrepresented remaining intents')
    return {'schema_version':1,'kind':'prepared_semantic_request','continuation':continuation.to_dict(),
        'actor':actor,'payload':_data(preparation.request.payload())}


def decode_remaining_completion(value):
    from .defaults import default_semantic_choice_registry
    if isinstance(value,Mapping) and value.get('kind')=='prepared_semantic_request':
        if set(value)!={'schema_version','kind','continuation','actor','payload'} or type(value['schema_version']) is not int or value['schema_version']!=1:
            raise SemanticChoiceError('Prepared semantic request has a closed schema')
        _handler,continuation=default_semantic_choice_registry().decode_continuation(value['continuation'])
        if type(value['actor']) is not str or not value['actor'] or not isinstance(value['payload'],Mapping):
            raise SemanticChoiceError('Prepared semantic request actor or payload is invalid')
        return continuation,value['actor'],{},None
    expected={'schema_version','kind','continuation','actor','response','intents','prepend_effects','repeat_effect'}
    if not isinstance(value,Mapping) or set(value)!=expected or type(value['schema_version']) is not int or value['schema_version']!=1 or value['kind']!='completed_semantic_choice':
        raise SemanticChoiceError('Completed semantic choice has a closed schema')
    _handler,continuation=default_semantic_choice_registry().decode_continuation(value['continuation'])
    if type(value['actor']) is not str or not value['actor'] or not isinstance(value['response'],Mapping):
        raise SemanticChoiceError('Completed semantic choice actor or response is invalid')
    if not isinstance(value['intents'],(list,tuple)) or not isinstance(value['prepend_effects'],(list,tuple)):
        raise SemanticChoiceError('Completed semantic choice sequences are invalid')
    intents=[]
    for encoded in value['intents']:
        if not isinstance(encoded,Mapping) or set(encoded)!={'kind','fields'}:
            raise SemanticChoiceError('Completed choice intent shape is invalid')
        cls=_intent_types().get(encoded['kind']);values=encoded['fields']
        if cls is None or not isinstance(values,Mapping) or set(values)!={field.name for field in fields(cls)}:
            raise SemanticChoiceError('Completed choice intent has unknown kind or fields')
        values={key:_restore(child) for key,child in values.items()}
        if 'transition_kind' in values:values['transition_kind']=ZoneTransitionKind(values['transition_kind'])
        intent=cls(**values)
        if _data({field.name:getattr(intent,field.name) for field in fields(intent)}) != _data(encoded['fields']):
            raise SemanticChoiceError('Completed choice intent data are not canonical')
        intents.append(intent)
    return continuation,value['actor'],dict(value['response']),SemanticChoiceCompletion(
        intents=tuple(intents),prepend_effects=tuple(value['prepend_effects']),repeat_effect=value['repeat_effect'])


def resume_completed_choice(host,value):
    from .counter_coordination import continue_semantic_completion
    continuation,actor,response,completion=decode_remaining_completion(value)
    item=next((item for item in host.state.stack if item.ref==continuation.stack_ref),None)
    if item is None:raise SemanticChoiceError('Completed choice parent stack object disappeared')
    frame=continuation.semantic_frame.to_dict()
    host._validate_semantic_frame(frame,item)
    if value['kind']=='prepared_semantic_request':
        decision=host.permissions.issue(kind='semantic.choice',role='pilot',actors=[actor],allowed_actions=['choose'],
            payload_by_actor={actor:dict(value['payload'])},continuation=continuation.to_dict())
        decision.continuation=continuation.with_pending_choice(decision.decision_id).to_dict()
        return
    continue_semantic_completion(host,item=item,continuation=continuation,actor=actor,
        response=response,completion=completion)


def validate_completed_choice_parent(host,value):
    continuation,_actor,_response,_completion=decode_remaining_completion(value)
    item=next((item for item in host.state.stack if item.ref==continuation.stack_ref),None)
    if item is None:raise SemanticChoiceError('Completed choice parent stack object disappeared')
    host._validate_semantic_frame(continuation.semantic_frame.to_dict(),item)


def finish_choice_intent_returns(host, waiting, saved):
    from ..conditional_exile_return import (
        return_after_source_departures, ConditionalReturnChoiceRequired, ConditionalReturnAuraChoiceRequired,
        issue_conditional_return_choice,issue_conditional_aura_choice,
    )
    bindings=tuple(binding for values,_sources in waiting for binding in values)
    sources=tuple(dict.fromkeys(source for _values,identities in waiting for source in identities))
    if not bindings:return False
    try:return_after_source_departures(host,bindings,sources)
    except ConditionalReturnChoiceRequired as required:
        issue_conditional_return_choice(host,required,saved);return True
    except ConditionalReturnAuraChoiceRequired as required:
        issue_conditional_aura_choice(host,required,saved);return True
    return False
