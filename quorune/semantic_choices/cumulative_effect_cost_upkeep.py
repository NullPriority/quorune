from __future__ import annotations

"""Collect per-age benefits before one all-or-none upkeep payment."""
from ..cumulative_effect_cost_upkeep import UpkeepEffectCostSpec
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import PlaceCountersIntent,AddManaIntent
from ..semantic_runtime.life_gain_cost_intent import LifeGainCostBatchIntent
from .source_maintenance import _declined_source_sacrifice
from .model import AutoContinue,ScalarChoice,SemanticChoiceCompletion,SemanticChoiceError,SemanticChoicePreparation,SemanticChoiceRequest

_PHASE={'stage','effect_cost_age','effect_cost_recipients'}
_PRIVATE={'_choice_actor','_stack_label','_source_logical_object_id'}
_TRANSPORT={'action','action_id','a','cap','reason','plan','next','choice_schema'}


def _spec(effect,actor):
    raw={k:v for k,v in effect.items() if k not in _PHASE|_PRIVATE}
    try:spec=UpkeepEffectCostSpec.from_effect(raw,actor=actor,source=effect.get('source'))
    except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
    stage=effect.get('stage');fields=set(effect)&_PHASE
    if stage is None and fields or stage=='pay' and fields!={'stage'} or stage=='choose' and fields!=_PHASE or stage not in {None,'pay','choose'}:
        raise SemanticChoiceError('Beneficial upkeep cost phase is malformed')
    if stage=='choose':_chosen(spec,effect)
    return spec,raw


def _chosen(spec,effect):
    age=effect['effect_cost_age'];spec.total(age);recipients=effect['effect_cost_recipients']
    if not isinstance(recipients,(tuple,list)) or len(recipients)>=age and recipients or any(type(p)is not str or not p for p in recipients):
        raise SemanticChoiceError('Beneficial upkeep cost declarations are malformed')
    return age,tuple(recipients)


def _source(effect,context):
    if context.actor!=context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError('Beneficial upkeep cost payer changed')
    identity=context.source_logical_object_id
    if type(identity)is not str or not identity:raise SemanticChoiceError('Beneficial upkeep lost source incarnation')
    source=context.query.object(context.source_ref,zones=('battlefield',)) if context.source_ref else None
    if source is None or source.phased_out or source.logical_object_id!=identity:
        if effect['source'] is not None:raise SemanticChoiceError('Beneficial upkeep source binding changed')
        return None
    if effect['source']!=source.ref:raise SemanticChoiceError('Beneficial upkeep source reference changed')
    return source


def _eligible_opponents(query,actor):
    return tuple(p for p in query.active_seats if p!=actor and query.life_gain_allowed(p))


def prepare_cumulative_effect_cost_upkeep(effect,context):
    if set(effect)&_PRIVATE:raise SemanticChoiceError('Beneficial upkeep preparation contains completion-only fields')
    spec,raw=_spec(effect,context.actor);source=_source(effect,context)
    if source is None:return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),auto_continue=AutoContinue('Original upkeep permanent left battlefield'))
    if 'stage' not in effect:
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=(PlaceCountersIntent(actor=context.actor,object_refs=(source.ref,),counter_name='age',amount=1,reason=context.stack_label,source_ref=source.ref),),auto_continue=AutoContinue('Commit age placement before pricing',prepend_effects=(FrozenMap({**raw,'stage':'pay'}),)))
    age,recipients=(source.counters.get('age',0),()) if effect['stage']=='pay' else _chosen(spec,effect)
    spec.total(age);opponents=_eligible_opponents(context.query,context.actor) if spec.kind=='opponent_life_gain' else ()
    if spec.kind=='add_mana' or age==0:
        choice=ScalarChoice(field_name='pay',legal_values=(True,False))
    else:choice=ScalarChoice(field_name='choice',legal_values=(*opponents,'decline'))
    stored={**raw,'stage':'choose','effect_cost_age':age,'effect_cost_recipients':list(recipients),'_choice_actor':context.actor,'_stack_label':context.stack_label,'_source_logical_object_id':context.source_logical_object_id}
    return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt='Pay the complete upkeep cost or sacrifice the permanent.',choice=choice,public_context=FrozenMap({'payment_kind':spec.kind,'age_counters':age,'chosen_costs':len(recipients),'unit_amount':spec.unit_amount,'mana_color':spec.color,'eligible_opponents':opponents})),continuation_effect=FrozenMap(stored))


def _validate_completion(continuation,query):
    effect=continuation.effect
    if set(effect)&_PRIVATE!=_PRIVATE:raise SemanticChoiceError('Beneficial upkeep completion identity is incomplete')
    actor=effect['_choice_actor'];spec,raw=_spec(effect,actor);age,recipients=_chosen(spec,effect)
    if effect.get('stage')!='choose' or actor!=continuation.semantic_frame.controller or actor not in query.active_seats or any(type(effect[k])is not str or not effect[k] for k in _PRIVATE):
        raise SemanticChoiceError('Beneficial upkeep completion identity changed')
    return effect,spec,raw,actor,age,recipients


def complete_cumulative_effect_cost_upkeep(continuation,response,query):
    effect,spec,raw,actor,age,recipients=_validate_completion(continuation,query)
    if spec.kind=='add_mana' or age==0:
        if set(response)-(_TRANSPORT|{'pay'}) or type(response.get('pay'))is not bool:raise SemanticChoiceError('Beneficial upkeep requires pay or decline')
        if not response['pay']:return _declined_source_sacrifice(effect,query)
        return SemanticChoiceCompletion(intents=tuple(AddManaIntent(player=actor,color=spec.color,amount=spec.unit_amount,actor=actor,reason=effect['_stack_label'],source_ref=effect['source']) for _ in range(age)))
    if set(response)-(_TRANSPORT|{'choice'}) or type(response.get('choice'))is not str:raise SemanticChoiceError('Choose an eligible opponent or decline')
    player=response['choice']
    if player=='decline':return _declined_source_sacrifice(effect,query)
    if player not in _eligible_opponents(query,actor):raise SemanticChoiceError('Beneficial upkeep selected an inactive, own or prohibited opponent')
    selected=(*recipients,player)
    if len(selected)<age:return SemanticChoiceCompletion(repeat_effect=FrozenMap({**raw,'stage':'choose','effect_cost_age':age,'effect_cost_recipients':list(selected)}))
    if any(p not in _eligible_opponents(query,actor) for p in selected):raise SemanticChoiceError('A prior chosen opponent is no longer eligible')
    return SemanticChoiceCompletion(intents=(LifeGainCostBatchIntent(actor=actor,players=selected,amount=spec.unit_amount,reason=effect['_stack_label'],source_ref=effect['source']),))
