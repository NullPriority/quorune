"""Mandatory untargeted Ring choice over immutable current creature facts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from ..ring_model import (
    RING_TEMPTATION_CAPABILITY, RING_TEMPTATION_OPERATION,
    RingBearerChoicePlan, RingStateError,
)
from ..semantic_runtime.ring_intent import BeginRingTemptationIntent, CompleteRingBearerIntent
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _validate(effect, actor):
    stage = effect.get('_ring_stage')
    if stage is not None and type(stage) is not str:
        raise SemanticChoiceError('Ring continuation stage must be exact text')
    expected = {'op','player'} | ({'_ring_stage'} if stage is not None else set())
    if stage == 'commit':expected.update(('_ring_plan','_ring_reason'))
    if (set(effect) != expected or effect.get('op') != RING_TEMPTATION_OPERATION
            or effect.get('player') != actor or stage not in {None,'choose','commit'}):
        raise SemanticChoiceError('Ring instruction or actor binding is malformed')
    return stage


def _retained(effect):
    try:
        plan = RingBearerChoicePlan.from_dict(thaw_value(effect['_ring_plan']))
    except (KeyError, RingStateError) as exc:
        raise SemanticChoiceError('Ring continuation has malformed retained facts') from exc
    if plan.state.player != effect['player']:
        raise SemanticChoiceError('Ring continuation player binding changed')
    if type(effect.get('_ring_reason')) is not str or not effect['_ring_reason'].strip():
        raise SemanticChoiceError('Ring continuation lost its effect provenance')
    return plan


def _same_progress(first, second):
    return (first.state.player,first.state.emblem,first.state.temptation_count) == (
        second.state.player,second.state.emblem,second.state.temptation_count)


@dataclass(frozen=True, slots=True)
class RingTemptationChoiceHandler:
    operation: str = RING_TEMPTATION_OPERATION
    handler_id: str = 'choice.public.ring-bearer.v1'
    schema_version: int = 1
    rule_references: tuple[str,...] = ('CR 701.54a','CR 701.54b','CR 701.54c','CR 701.54d','CR 701.54e')
    capability_dependencies: tuple[str,...] = (RING_TEMPTATION_CAPABILITY,)
    continuation_fields: tuple[str,...] = ('player','_ring_stage','_ring_plan','_ring_reason')
    private_data: tuple[str,...] = ()
    projected_fields: tuple[str,...] = ('prompt','legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str,...] = ('BeginRingTemptationIntent','CompleteRingBearerIntent')
    replay_fixture: str = 'ring-temptation'
    test_modules: tuple[str,...] = ('tests.test_ring_temptation_choices', 'tests.test_ring_native_actions')

    def prepare(self, effect: Mapping[str,Any], context):
        stage = _validate(effect,context.actor)
        if stage is None:
            next_effect = FrozenMap({'op':self.operation,'player':context.actor,'_ring_stage':'choose'})
            return SemanticChoicePreparation(
                request=None,continuation_effect=FrozenMap(effect),
                preparation_intents=(BeginRingTemptationIntent(context.actor,context.stack_label),),
                auto_continue=AutoContinue(reason='Prepare Ring progress before choosing a creature',
                                           prepend_effects=(next_effect,)))
        current = context.query.ring_choice_plan(context.actor)
        if stage == 'commit' and not _same_progress(_retained(effect),current):
            raise SemanticChoiceError('Pending Ring progress changed')
        continuation = FrozenMap({'op':self.operation,'player':context.actor,
                                  '_ring_stage':'commit','_ring_plan':current.to_dict(),
                                  '_ring_reason':context.stack_label})
        if not current.needs_choice:
            return SemanticChoicePreparation(
                request=None,continuation_effect=continuation,
                preparation_intents=(CompleteRingBearerIntent(current,None,context.stack_label),),
                auto_continue=AutoContinue(reason='Temptation completes without an available creature'))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt='Choose a creature you control as your Ring-bearer.',
                choice=ObjectChoice(field_name='objects',
                    legal_refs=tuple(row.identity.object_ref for row in current.candidates),
                    zones=('battlefield',),minimum=1,maximum=1,visibility='public',
                    owner_relation='any',controller_relation='actor'),
                public_context=FrozenMap({'stack':context.stack_ref,'operation':self.operation})),
            continuation_effect=continuation)

    def complete(self, continuation, response: Mapping[str,Any], query):
        effect = continuation.effect
        if _validate(effect,effect.get('player')) != 'commit':
            raise SemanticChoiceError('Ring completion requires a retained creature choice')
        issued = _retained(effect)
        current = query.ring_choice_plan(issued.state.player)
        if not _same_progress(issued,current):
            raise SemanticChoiceError('Pending Ring progress changed')
        if 'objects' not in response or set(response)-{'objects','action_id','action','choice_schema'}:
            raise SemanticChoiceError('Ring choice requires its exact object response')
        refs = response['objects']
        if not isinstance(refs,(tuple,list)) or len(refs)!=1 or type(refs[0]) is not str:
            raise SemanticChoiceError('Ring choice requires exactly one creature reference')
        offered = next((row for row in issued.candidates if row.identity.object_ref==refs[0]),None)
        live = next((row for row in current.candidates if offered is not None and row.identity==offered.identity),None)
        if offered is None or live is None or not offered.control_is_continuous(live.history):
            raise SemanticChoiceError('The chosen Ring-bearer is no longer a legal sealed choice')
        return SemanticChoiceCompletion(intents=(CompleteRingBearerIntent(issued,refs[0],effect['_ring_reason']),))
