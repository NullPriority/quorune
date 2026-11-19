from __future__ import annotations

"""Declare per-age colors without payment, then reuse ordinary source upkeep."""
from collections.abc import Mapping
from ..cumulative_alternative_mana_upkeep import CumulativeAlternativeManaSpec
from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..source_maintenance import SourceMaintenanceSpec
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import PlaceCountersIntent
from .source_maintenance import _declined_source_sacrifice
from .model import AutoContinue,ScalarChoice,SemanticChoiceCompletion,SemanticChoiceError,SemanticChoicePreparation,SemanticChoiceRequest

_PHASE={'stage','upkeep_age','color_counts'}
_PRIVATE={'_choice_actor','_stack_label','_source_logical_object_id'}
_TRANSPORT={'action','action_id','a','cap','reason','plan','next','choice_schema'}


def _spec(effect,actor):
    raw={k:v for k,v in effect.items() if k not in _PHASE|_PRIVATE}
    try:spec=CumulativeAlternativeManaSpec.from_effect(raw,actor=actor,source=effect.get('source'))
    except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
    stage=effect.get('stage');fields=set(effect)&_PHASE
    if stage is None and fields or stage=='price' and fields!={'stage'} or stage=='declare' and fields!=_PHASE or stage not in {None,'price','declare'}:
        raise SemanticChoiceError('Alternative upkeep phase is malformed')
    if stage=='declare':spec.declared_count(effect['upkeep_age'],effect['color_counts'])
    return spec,raw


def _original_source(effect,context):
    if context.actor!=context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError('Alternative upkeep trigger controller changed')
    identity=context.source_logical_object_id
    if type(identity)is not str or not identity:raise SemanticChoiceError('Alternative upkeep lost its source incarnation')
    source=context.query.object(context.source_ref,zones=('battlefield',)) if context.source_ref else None
    if source is None or source.phased_out or source.logical_object_id!=identity:
        if effect['source'] is not None:raise SemanticChoiceError('Alternative upkeep source binding changed')
        return None
    if effect['source']!=source.ref:raise SemanticChoiceError('Alternative upkeep source reference changed')
    return source


def prepare_cumulative_alternative_mana_upkeep(effect,context):
    if set(effect)&_PRIVATE:raise SemanticChoiceError('Alternative upkeep has completion-only fields')
    spec,raw=_spec(effect,context.actor);source=_original_source(effect,context)
    if source is None:return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),auto_continue=AutoContinue('Original upkeep permanent has left'))
    if 'stage' not in effect:
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=(PlaceCountersIntent(actor=context.actor,object_refs=(source.ref,),counter_name='age',amount=1,reason=context.stack_label,source_ref=source.ref),),auto_continue=AutoContinue('Commit replacement-aware age placement before choosing costs',prepend_effects=(FrozenMap({**dict(raw),'stage':'price'}),)))
    age,counts=(source.counters.get('age',0),{}) if effect['stage']=='price' else (effect['upkeep_age'],effect['color_counts'])
    total=spec.declared_count(age,counts)
    if age>0 and total==age:
        payment=FixedEffectPaymentSpec('mana',requirements=FrozenMap(spec.requirements(age,counts)))
        resolved=SourceMaintenanceSpec(payment).effect();resolved.update(player=context.actor,source=source.ref)
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),auto_continue=AutoContinue('The whole declared price is ready for ordinary payment',prepend_effects=(FrozenMap(resolved),)))
    stored={**raw,'stage':'declare','upkeep_age':age,'color_counts':dict(counts),'_choice_actor':context.actor,'_stack_label':context.stack_label,'_source_logical_object_id':context.source_logical_object_id}
    choice=ScalarChoice(field_name='pay',legal_values=(True,False)) if age==0 else ScalarChoice(field_name='choice',legal_values=(*spec.colors,'decline'))
    prompt='Pay the empty upkeep cost or sacrifice the permanent.' if age==0 else f'Choose a color for upkeep cost {total+1} of {age}, or decline.'
    options=[{'id':color,'label':'Declare {'+color+'}'} for color in spec.colors]+[{'id':'decline','label':'Decline upkeep'}]
    return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt=prompt,choice=choice,public_context=FrozenMap({'payment_kind':'alternative_color_mana','age_counters':age,'declared_costs':total,'options':options if age else []})),continuation_effect=FrozenMap(stored))


def complete_cumulative_alternative_mana_upkeep(continuation,response,query):
    effect=continuation.effect
    if set(effect)&_PRIVATE!=_PRIVATE:raise SemanticChoiceError('Alternative upkeep completion identity is incomplete')
    actor=effect['_choice_actor'];spec,raw=_spec(effect,actor)
    if effect.get('stage')!='declare' or actor!=continuation.semantic_frame.controller or actor not in query.active_seats or any(type(effect[k])is not str or not effect[k] for k in _PRIVATE):
        raise SemanticChoiceError('Alternative upkeep completion identity changed')
    age,counts=effect['upkeep_age'],effect['color_counts']
    if age==0:
        if set(response)-(_TRANSPORT|{'pay'}) or type(response.get('pay'))is not bool:raise SemanticChoiceError('Empty upkeep requires pay or decline')
        return SemanticChoiceCompletion() if response['pay'] else _declined_source_sacrifice(effect,query)
    if set(response)-(_TRANSPORT|{'choice'}) or type(response.get('choice'))is not str:
        raise SemanticChoiceError('Choose one printed upkeep color or decline')
    color=response['choice']
    if color=='decline':return _declined_source_sacrifice(effect,query)
    try:chosen=spec.propose(age,counts,color)
    except ValueError as exc:raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(repeat_effect=FrozenMap({**raw,'stage':'declare','upkeep_age':age,'color_counts':chosen}))
