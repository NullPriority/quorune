from __future__ import annotations

"""Commit age placement, then delegate one whole cost to source maintenance."""
from dataclasses import replace
from ..cumulative_object_upkeep import CumulativeObjectUpkeepSpec
from ..source_maintenance import SourceMaintenanceSpec
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import PlaceCountersIntent
from .source_maintenance import prepare_source_maintenance,_declined_source_sacrifice
from .model import AutoContinue,ScalarChoice,SemanticChoiceCompletion,SemanticChoiceError,SemanticChoicePreparation,SemanticChoiceRequest

_ZERO_FIELDS={'_zero_age_payment','_choice_actor','_stack_label','_source_logical_object_id','_legal_objects'}


def _spec(effect,actor):
    raw={k:v for k,v in effect.items() if k!='stage'}
    if 'stage' in effect and effect['stage']!='pay':raise SemanticChoiceError('Unknown object-upkeep phase')
    try:return CumulativeObjectUpkeepSpec.from_effect(raw,actor=actor,source=effect.get('source'))
    except (ValueError,KeyError,TypeError) as exc:raise SemanticChoiceError(str(exc)) from exc


def prepare_cumulative_object_upkeep(effect,context):
    spec=_spec(effect,context.actor)
    if context.actor!=context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError('Object upkeep changed its trigger controller')
    identity=context.source_logical_object_id
    if type(identity)is not str or not identity:raise SemanticChoiceError('Object upkeep lost its source incarnation')
    source=context.query.object(context.source_ref,zones=('battlefield',)) if context.source_ref else None
    if source is None or source.phased_out or source.logical_object_id!=identity:
        if effect['source'] is not None:raise SemanticChoiceError('Object upkeep source binding changed')
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),auto_continue=AutoContinue('Original upkeep permanent left battlefield'))
    if effect['source']!=source.ref:raise SemanticChoiceError('Object upkeep source reference changed')
    if 'stage' not in effect:
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=(PlaceCountersIntent(actor=context.actor,object_refs=(source.ref,),counter_name='age',amount=1,reason=context.stack_label,source_ref=context.source_ref),),auto_continue=AutoContinue('Commit replacement-aware age counter',prepend_effects=(FrozenMap({**dict(effect),'stage':'pay'}),)))
    age=source.counters.get('age',0);payment=spec.repeated_payment(age)
    if payment is not None:
        maintenance=SourceMaintenanceSpec(payment).effect();maintenance.update(player=context.actor,source=source.ref)
        return prepare_source_maintenance(maintenance,context)
    stored={**dict(effect),'_zero_age_payment':True,'_choice_actor':context.actor,'_stack_label':context.stack_label,'_source_logical_object_id':identity,'_legal_objects':[]}
    return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt='Pay the empty cumulative-upkeep cost or sacrifice the permanent.',choice=ScalarChoice(field_name='pay',legal_values=(True,False)),public_context=FrozenMap({'payment_kind':spec.payment.kind,'age_counters':0,'payable':True})),continuation_effect=FrozenMap(stored))


def complete_zero_age_upkeep(continuation,response,query):
    effect=continuation.effect
    if not _ZERO_FIELDS<=set(effect) or effect['_zero_age_payment']is not True or effect['_legal_objects'] not in ([],()):
        raise SemanticChoiceError('Empty upkeep continuation is malformed')
    raw={k:v for k,v in effect.items() if k not in _ZERO_FIELDS};actor=effect['_choice_actor'];_spec(raw,actor)
    if raw.get('stage')!='pay' or actor!=continuation.semantic_frame.controller or actor not in query.active_seats or type(effect['_source_logical_object_id'])is not str or not effect['_source_logical_object_id'] or type(effect['_stack_label'])is not str or not effect['_stack_label']:
        raise SemanticChoiceError('Empty upkeep continuation identity changed')
    allowed={'pay','action','action_id','a','cap','reason','plan','next','choice_schema'}
    if set(response)-allowed or 'pay' not in response or type(response['pay'])is not bool:raise SemanticChoiceError('Empty upkeep requires pay or decline')
    return SemanticChoiceCompletion() if response['pay'] else _declined_source_sacrifice(effect,query)
