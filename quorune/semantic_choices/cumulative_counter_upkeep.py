from __future__ import annotations

"""Collect per-age choices, then pay one replacement-aware counter batch."""
from ..cumulative_counter_upkeep import CumulativeCounterUpkeepSpec
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import PlaceCountersIntent
from .source_maintenance import _declined_source_sacrifice
from .model import AutoContinue,ObjectChoice,ScalarChoice,SemanticChoiceCompletion,SemanticChoiceError,SemanticChoicePreparation,SemanticChoiceRequest

_PHASE={'stage','counter_required','counter_chosen'}
_PRIVATE={'_choice_actor','_stack_label','_source_logical_object_id','_legal_objects'}
_TRANSPORT={'action','action_id','a','cap','reason','plan','next','choice_schema'}


def _spec(effect,actor):
    raw={k:v for k,v in effect.items() if k not in _PHASE|_PRIVATE}
    try:spec=CumulativeCounterUpkeepSpec.from_effect(raw,actor=actor,source=effect.get('source'))
    except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
    stage=effect.get('stage')
    phase=set(effect)&_PHASE
    if stage is None and phase or stage=='pay' and phase!={'stage'} or stage=='select' and phase!=_PHASE or stage not in {None,'pay','select'}:
        raise SemanticChoiceError('Counter upkeep phase is malformed')
    if stage=='select':_selection(effect)
    return spec,raw


def _selection(effect):
    required=effect['counter_required'];chosen=effect['counter_chosen']
    if type(required)is not int or required<0 or not isinstance(chosen,(tuple,list)) or len(chosen)>=required and chosen:
        raise SemanticChoiceError('Counter upkeep cost count is malformed')
    for row in chosen:
        if not isinstance(row,dict) and not isinstance(row,FrozenMap) or set(row)!={'ref','logical_object_id'} or any(type(row[k])is not str or not row[k] or row[k]!=row[k].strip() for k in row):
            raise SemanticChoiceError('Counter upkeep recipient seal is malformed')
    return required,tuple(chosen)


def _eligible(row,cost,actor,source_ref,query):
    if row is None or row.zone!='battlefield' or row.phased_out:return False
    relation=row.ref==source_ref if cost.recipient_scope=='source' else row.controller in query.active_seats and row.controller!=actor and 'creature' in row.types
    return relation and query.counter_placement_allowed(row.ref,cost.counter_name,1)


def _candidates(cost,actor,source_ref,query):
    return tuple(sorted((row for row in query.objects(zones=('battlefield',)) if _eligible(row,cost,actor,source_ref,query)),key=lambda row:row.ref))


def _identity(context,effect):
    if context.actor!=context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError('Counter upkeep trigger controller changed')
    identity=context.source_logical_object_id
    if type(identity)is not str or not identity:raise SemanticChoiceError('Counter upkeep lost its original source incarnation')
    source=context.query.object(context.source_ref,zones=('battlefield',)) if context.source_ref else None
    if source is None or source.phased_out or source.logical_object_id!=identity:
        if effect['source'] is not None:raise SemanticChoiceError('Counter upkeep source binding changed')
        return None
    if effect['source']!=source.ref:raise SemanticChoiceError('Counter upkeep source reference changed')
    return source


def prepare_cumulative_counter_upkeep(effect,context):
    if set(effect)&_PRIVATE:raise SemanticChoiceError('Counter upkeep preparation has completion-only fields')
    spec,_raw=_spec(effect,context.actor);source=_identity(context,effect)
    if source is None:return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),auto_continue=AutoContinue('Original upkeep permanent has left'))
    if 'stage' not in effect:
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=(PlaceCountersIntent(actor=context.actor,object_refs=(source.ref,),counter_name='age',amount=1,reason=context.stack_label,source_ref=source.ref),),auto_continue=AutoContinue('Commit the age counter before pricing',prepend_effects=(FrozenMap({**dict(effect),'stage':'pay'}),)))
    if effect['stage']=='pay':
        required=source.counters.get('age',0);chosen=()
        if type(required)is not int or required<0:raise SemanticChoiceError('Committed age count is invalid')
    else:required,chosen=_selection(effect)
    return _offer(spec,effect,context,required,chosen)


def _offer(spec,effect,context,required,chosen):
    candidates=_candidates(spec.cost,context.actor,context.source_ref,context.query)
    payable=required==0 or bool(candidates)
    if spec.cost.recipient_scope=='source' or required==0:
        choice=ScalarChoice(field_name='pay',legal_values=(True,False) if payable else (False,))
    else:
        choice=ObjectChoice(field_name='cards',legal_refs=tuple(r.ref for r in candidates),zones=('battlefield',),minimum=0,maximum=1 if payable else 0,optional=True)
    stored={**dict(effect),'stage':'select','counter_required':required,'counter_chosen':list(chosen),'_choice_actor':context.actor,'_stack_label':context.stack_label,'_source_logical_object_id':context.source_logical_object_id,'_legal_objects':[{'ref':r.ref,'logical_object_id':r.logical_object_id} for r in candidates]}
    return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt='Pay the full counter upkeep cost or sacrifice the permanent.',choice=choice,public_context=FrozenMap({'payment_kind':'counter_placement','counter':spec.cost.counter_name,'age_counters':required,'chosen_costs':len(chosen),'payable':payable})),continuation_effect=FrozenMap(stored))


def _validate_completion(continuation,query):
    effect=continuation.effect
    if set(effect)&_PRIVATE!=_PRIVATE:raise SemanticChoiceError('Counter upkeep completion identity is incomplete')
    actor=effect['_choice_actor'];spec,raw=_spec(effect,actor);required,chosen=_selection(effect)
    if effect.get('stage')!='select' or actor!=continuation.semantic_frame.controller or actor not in query.active_seats or any(type(effect[k])is not str or not effect[k] for k in ('_choice_actor','_stack_label','_source_logical_object_id')):
        raise SemanticChoiceError('Counter upkeep completion identity changed')
    seals=effect['_legal_objects']
    if not isinstance(seals,(list,tuple)) or any(not isinstance(r,(dict,FrozenMap)) or set(r)!={'ref','logical_object_id'} or any(type(r[k])is not str or not r[k] for k in r) for r in seals) or len({r['ref'] for r in seals})!=len(seals):
        raise SemanticChoiceError('Counter upkeep offer seals are malformed')
    return spec,raw,actor,required,chosen,{r['ref']:r['logical_object_id'] for r in seals}


def _pay(spec,effect,actor,refs,seals,query):
    for ref in refs:
        row=query.object(ref,zones=('battlefield',))
        if not _eligible(row,spec.cost,actor,effect['source'],query) or seals.get(ref)!=row.logical_object_id:
            raise SemanticChoiceError('Counter upkeep selected a stale or prohibited recipient')
    if not refs:return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(intents=(PlaceCountersIntent(actor=actor,object_refs=tuple(refs),counter_name=spec.cost.counter_name,amount=1,reason=effect['_stack_label'],source_ref=effect['source'],effect_generated=False,ordered_cost_version=2),))


def complete_cumulative_counter_upkeep(continuation,response,query):
    spec,raw,actor,required,chosen,seals=_validate_completion(continuation,query);effect=continuation.effect
    if spec.cost.recipient_scope=='source' or required==0:
        if set(response)-(_TRANSPORT|{'pay'}) or type(response.get('pay'))is not bool:raise SemanticChoiceError('Counter upkeep requires pay or decline')
        if not response['pay']:return _declined_source_sacrifice(effect,query)
        return _pay(spec,effect,actor,(effect['source'],)*required,seals,query)
    if set(response)-(_TRANSPORT|{'cards','pay'}):raise SemanticChoiceError('Counter upkeep recipient response has unknown fields')
    selected=response.get('cards',())
    if not isinstance(selected,(list,tuple)) or len(selected)>1 or any(type(ref)is not str for ref in selected) or 'pay' in response and type(response['pay'])is not bool:
        raise SemanticChoiceError('Choose exactly one recipient for each age cost, or decline')
    if not selected:
        if response.get('pay',False):raise SemanticChoiceError('Counter upkeep has no selected payment')
        return _declined_source_sacrifice(effect,query)
    if response.get('pay',True)is not True:raise SemanticChoiceError('Counter upkeep selection conflicts with decline')
    ref=selected[0];row=query.object(ref,zones=('battlefield',))
    if not _eligible(row,spec.cost,actor,effect['source'],query) or seals.get(ref)!=row.logical_object_id:
        raise SemanticChoiceError('Counter upkeep recipient is stale or prohibited')
    proposed=(*chosen,{'ref':ref,'logical_object_id':row.logical_object_id})
    for prior in proposed:
        current=query.object(prior['ref'],zones=('battlefield',))
        if current is None or current.logical_object_id!=prior['logical_object_id']:
            raise SemanticChoiceError('Earlier counter cost recipient changed incarnation')
    all_seals={r['ref']:r['logical_object_id'] for r in proposed}
    if len(proposed)<required:
        return SemanticChoiceCompletion(repeat_effect=FrozenMap({**raw,'stage':'select','counter_required':required,'counter_chosen':list(proposed)}))
    return _pay(spec,effect,actor,tuple(r['ref'] for r in proposed),all_seals,query)
