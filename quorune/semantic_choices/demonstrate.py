"""Demonstrate delegates mutation and retargeting to the canonical stack-copy owner."""
from collections.abc import Mapping

from ..demonstrate_model import DemonstrateState, DemonstrateStage
from ..event_spell_copy_model import EventSpellCopySource
from ..replacement.immutable import FrozenMap
from .model import (AutoContinue, ScalarChoice, SemanticChoiceCompletion,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest)

_STATE = 'demonstrate_state'


def _state(effect, actor, stack_ref):
    from ..rules.demonstrate import DEMONSTRATE_FIELD
    fields = {'op', 'player', DEMONSTRATE_FIELD, 'copy_source', 'stack'}
    if set(effect) not in (fields, fields | {_STATE}):
        raise SemanticChoiceError('Demonstrate continuation fields are closed')
    marker = effect.get(DEMONSTRATE_FIELD)
    if (effect.get('op') != 'choose_objects' or effect.get('player') != actor
            or not isinstance(marker, Mapping) or set(marker) != {'schema_version'}
            or type(marker['schema_version']) is not int or marker['schema_version'] != 1):
        raise SemanticChoiceError('Demonstrate requires its current choice actor and explicit version')
    try:
        source = EventSpellCopySource.from_dict(effect['copy_source'])
        state = (DemonstrateState.from_dict(effect[_STATE]) if _STATE in effect
                 else DemonstrateState(actor, stack_ref, source))
        if (state.actor != actor or state.trigger_ref != stack_ref or state.source != source
                or effect['stack'] != source.item().ref):
            raise ValueError('Demonstrate continuation changed actor, trigger or original source')
        return state
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _effect(effect, state):
    return FrozenMap({**dict(effect), _STATE: state.to_dict()})


def _copy_instruction(state, actor):
    return FrozenMap({'op': 'copy_stack_item', 'player': actor,
        'stack': state.source.item().ref, 'copy_source': state.source.to_dict()})


def prepare_demonstrate(effect, context):
    state = _state(effect, context.actor, context.stack_ref)
    saved = _effect(effect, state)
    if state.stage is DemonstrateStage.SELF_COPY:
        values = ('copy', 'decline')
        prompt = 'Copy this spell with demonstrate?'
    else:
        values = state.opponents(context.query.seats)
        if not values:
            return SemanticChoicePreparation(request=None, continuation_effect=saved,
                auto_continue=AutoContinue(reason='No opponent remains to copy the spell'))
        prompt = 'Choose an opponent to copy the original spell.'
    return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt=prompt,
        choice=ScalarChoice(field_name='choice', legal_values=values),
        public_context=FrozenMap({'stack': context.stack_ref, 'operation': 'choose_objects'})),
        continuation_effect=saved)


def complete_demonstrate(continuation, response, query):
    raw = continuation.effect.get(_STATE)
    if not isinstance(raw, Mapping):
        raise SemanticChoiceError('Demonstrate choice has no saved state')
    actor = raw.get('actor')
    state = _state(continuation.effect, actor, continuation.stack_ref)
    if actor not in query.seats:
        raise SemanticChoiceError('Demonstrate controller is no longer active')
    choice = response.get('choice')
    if state.stage is DemonstrateStage.SELF_COPY:
        if choice == 'decline':
            return SemanticChoiceCompletion()
        if choice != 'copy':
            raise SemanticChoiceError('Choose copy or decline')
        # Resolution completes the canonical copy instruction (including its
        # target choice and committed CopyStackItemIntent) before this next
        # instruction can offer the mandatory opponent choice. Copy errors
        # therefore never enqueue a successful opponent stage.
        return SemanticChoiceCompletion(prepend_effects=(
            _copy_instruction(state, actor),
            _effect(continuation.effect, state.after_committed_self_copy())))
    if choice not in state.opponents(query.seats):
        raise SemanticChoiceError('Choose a current opponent')
    return SemanticChoiceCompletion(prepend_effects=(_copy_instruction(state, choice),))
