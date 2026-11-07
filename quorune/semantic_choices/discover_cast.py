from __future__ import annotations

"""Sealed exile-cast choices within an enclosing instruction list."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..discover_model import (DISCOVER_CAPABILITY, DISCOVER_CAST_OPERATION, EXILE_CAST_CHOICE_OPERATIONS,
    EXILE_CAST_FINISH_OPERATIONS, ExileCastProgress,DiscoverCastIntent,DiscoverCleanupIntent,discover_options_fingerprint,
    discover_zone_counter,require_current_discover_cards)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
from .model import (AutoContinue,CastOrDeclineChoice,SemanticChoiceCompletion,SemanticChoiceError,
    SemanticChoicePreparation,SemanticChoiceRequest)


def _finish_effect(actor,progress):
    return FrozenMap({'op':progress.finish_operation,'player':actor,'progress':progress.to_dict()})


def _hand_intent(actor,progress):
    candidate=progress.candidate
    return ZoneMoveIntent(actor,candidate.ref,('exile',),'hand','Discover',owned_only=True,
        expected_zone_change_counter=discover_zone_counter(candidate))


def _decline(actor, progress):
    if progress.supplier is not None:
        return (), (_finish_effect(actor, replace(progress, candidate_ref=None)),)
    if progress.amount is not None:
        return (_hand_intent(actor, progress),), (_finish_effect(actor, progress),)
    return (), ()


def _progress(effect,actor,query,*,finishing=False):
    if effect.get('player')!=actor or actor not in query.active_seats:
        raise SemanticChoiceError('Discover requires the resolving controller')
    progress = ExileCastProgress.from_dict(effect['progress'])
    expected = progress.finish_operation if finishing else progress.cast_operation
    if expected != effect.get('op'):
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
        finishing = self.operation in EXILE_CAST_FINISH_OPERATIONS
        progress=_progress(effect,context.actor,context.query,finishing=finishing)
        if finishing:
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
                preparation_intents=(DiscoverCleanupIntent(context.actor,context.stack_ref,progress),),
                auto_continue=AutoContinue(reason='The Discover procedure completed; resume the enclosing instruction.'))
        if self.operation not in EXILE_CAST_CHOICE_OPERATIONS:
            raise SemanticChoiceError('Unknown resolving exile-cast choice operation')
        options=tuple(context.query.authorized_cast_options())
        if not options:
            intents, finish = _decline(context.actor, progress)
            reason = 'The discovered card cannot be cast and goes to hand.' if progress.amount is not None else (
                'The supplied card cannot be cast and returns with the other exiled cards.' if finish
                else 'The supplied card cannot be cast and stays exiled.')
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
                preparation_intents=intents, auto_continue=AutoContinue(reason=reason, prepend_effects=finish))
        if progress.supplier is not None:
            prompt = 'Cast the exiled card without paying its mana cost, or return it with the other exiled cards?'
        elif progress.amount is not None:
            prompt = 'Cast the discovered card without paying its mana cost, or put it into your hand?'
        else:
            prompt = 'Cast the exiled card without paying its mana cost, or leave it exiled?'
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt=prompt,
            choice=CastOrDeclineChoice(options),public_context=FrozenMap({'stack':context.stack_ref,
                'operation':self.operation,'card':progress.candidate_ref,'cast_options':list(options)})),
            continuation_effect=FrozenMap({**effect,'_choice_actor':context.actor,'_stack_ref':context.stack_ref,
                '_options_fingerprint':discover_options_fingerprint(options)}))

    def complete(self,continuation,response,query):
        effect=continuation.effect
        if (self.operation not in EXILE_CAST_CHOICE_OPERATIONS
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
            finish = (_finish_effect(actor, progress),) if progress.finish_operation is not None else ()
        else:
            intents, finish = _decline(actor, progress)
        return SemanticChoiceCompletion(intents=intents, prepend_effects=finish)
