from __future__ import annotations

"""Sealed exile-cast choices within an enclosing instruction list."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..discover_model import (DISCOVER_CAPABILITY,DISCOVER_CAST_OPERATION,DISCOVER_FINISH_OPERATION,
    NONLAND_SCAN_CAST_OPERATION, ExileCastProgress,DiscoverCastIntent,DiscoverCleanupIntent,discover_options_fingerprint,
    discover_zone_counter,require_current_discover_cards)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
from .model import (AutoContinue,CastOrDeclineChoice,SemanticChoiceCompletion,SemanticChoiceError,
    SemanticChoicePreparation,SemanticChoiceRequest)


def _finish_effect(actor,progress):
    return FrozenMap({'op':DISCOVER_FINISH_OPERATION,'player':actor,'progress':progress.to_dict()})


def _hand_intent(actor,progress):
    candidate=progress.candidate
    return ZoneMoveIntent(actor,candidate.ref,('exile',),'hand','Discover',owned_only=True,
        expected_zone_change_counter=discover_zone_counter(candidate))


def _progress(effect,actor,query,*,finishing=False):
    if effect.get('player')!=actor or actor not in query.active_seats:
        raise SemanticChoiceError('Discover requires the resolving controller')
    progress = ExileCastProgress.from_dict(effect['progress'])
    if (progress.amount is None) != (effect.get('op') == NONLAND_SCAN_CAST_OPERATION):
        raise SemanticChoiceError('The exile cast changed its printed search rule')
    if not finishing and progress.candidate is None:
        raise SemanticChoiceError('Discover casting requires its final discovered card')
    require_current_discover_cards(progress,query,actor=actor,include_candidate=not finishing)
    return progress


@dataclass(frozen=True,slots=True)
class DiscoverCastChoiceHandler:
    operation: str=DISCOVER_CAST_OPERATION
    handler_id: str='choice.library.discover-cast.v1'
    schema_version: int=1
    rule_references: tuple[str,...]=('202.3','400.7','608.2g','701.57a','701.57b','701.57c')
    capability_dependencies: tuple[str,...]=(DISCOVER_CAPABILITY,)
    continuation_fields: tuple[str,...]=('player','progress','_choice_actor','_stack_ref','_options_fingerprint')
    private_data: tuple[str,...]=()
    projected_fields: tuple[str,...]=('prompt','card','cast_options','legal_actions.choice_schema.legal_values',
        'legal_actions.choice_schema.cast_options','legal_actions.choice_schema.cast_options_when')
    mutation_path: tuple[str,...]=('DiscoverCastIntent','ZoneMoveIntent','DiscoverCleanupIntent')
    replay_fixture: str='discover-controller-cast-and-enclosing-resolution'
    test_modules: tuple[str,...]=('tests.test_discover_rules',)

    def prepare(self,effect,context):
        if set(effect)!={'op','player','progress'} or effect['op']!=self.operation or context.actor!=context.stack_controller:
            raise SemanticChoiceError('Discover choice has a closed source-bound shape')
        progress=_progress(effect,context.actor,context.query,finishing=self.operation==DISCOVER_FINISH_OPERATION)
        if self.operation==DISCOVER_FINISH_OPERATION:
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
                preparation_intents=(DiscoverCleanupIntent(context.actor,context.stack_ref,progress),),
                auto_continue=AutoContinue(reason='The Discover procedure completed; resume the enclosing instruction.'))
        if self.operation not in {DISCOVER_CAST_OPERATION, NONLAND_SCAN_CAST_OPERATION}:
            raise SemanticChoiceError('Unknown resolving exile-cast choice operation')
        nonland = self.operation == NONLAND_SCAN_CAST_OPERATION
        options=tuple(context.query.authorized_cast_options())
        if not options:
            if nonland:
                return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                    auto_continue=AutoContinue(reason='The supplied card cannot be cast and stays exiled.'))
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
                preparation_intents=(_hand_intent(context.actor,progress),),
                auto_continue=AutoContinue(reason='The discovered card cannot be cast and goes to hand.',
                    prepend_effects=(_finish_effect(context.actor,progress),)))
        prompt = ('Cast the exiled card without paying its mana cost, or leave it exiled?' if nonland
                  else 'Cast the discovered card without paying its mana cost, or put it into your hand?')
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt=prompt,
            choice=CastOrDeclineChoice(options),public_context=FrozenMap({'stack':context.stack_ref,
                'operation':self.operation,'card':progress.candidate_ref,'cast_options':list(options)})),
            continuation_effect=FrozenMap({**effect,'_choice_actor':context.actor,'_stack_ref':context.stack_ref,
                '_options_fingerprint':discover_options_fingerprint(options)}))

    def complete(self,continuation,response,query):
        effect=continuation.effect
        if (self.operation not in {DISCOVER_CAST_OPERATION, NONLAND_SCAN_CAST_OPERATION}
                or set(effect)!={'op','player','progress',
                '_choice_actor','_stack_ref','_options_fingerprint'} or effect['op']!=self.operation
                or effect['_stack_ref']!=continuation.stack_ref or effect['_choice_actor']!=effect['player']):
            raise SemanticChoiceError('Discover choice continuation changed')
        actor=effect['_choice_actor'];progress=_progress(effect,actor,query)
        options=tuple(query.authorized_cast_options())
        if discover_options_fingerprint(options)!=effect['_options_fingerprint']:
            raise SemanticChoiceError('Discover casting options changed')
        choice=response.get('choice');cast=response.get('cast')
        if choice not in {'cast','decline'} or cast is not None and not isinstance(cast,Mapping):
            raise SemanticChoiceError('Choose one current Discover action')
        if choice == 'cast':
            intents = (DiscoverCastIntent(actor, continuation.stack_ref, progress,
                                         effect['_options_fingerprint'], FrozenMap(cast or {})),)
        elif progress.amount is None:
            intents = ()
        else:
            intents = (_hand_intent(actor, progress),)
        finish = () if progress.amount is None else (_finish_effect(actor, progress),)
        return SemanticChoiceCompletion(intents=intents, prepend_effects=finish)
