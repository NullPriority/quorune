"""Read-only Ripple reveal/cast/bottom choices over existing typed mutation intents."""
from dataclasses import replace
from collections.abc import Mapping
from ..replacement.immutable import FrozenMap
from ..discover_model import discover_options_fingerprint
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.ripple import RIPPLE_FIELD,decode_ripple_instruction
from ..rules.ripple_resolution_model import (
    RIPPLE_STATE_FIELD,RippleResolutionState,ResolveRippleCastIntent,RippleBottomIntent,
)
from .model import (
    AutoContinue,ScalarChoice,ReferenceSetChoice,CastOrDeclineChoice,
    SemanticChoicePreparation,SemanticChoiceCompletion,SemanticChoiceRequest,SemanticChoiceError,
)

_OPTIONS='_options_fingerprint'


def _state(effect,actor,stack_ref):
    state=RippleResolutionState.from_effect(effect,actor=actor)
    if state.stack_ref!=stack_ref:raise ValueError('Ripple choice changed its resolving trigger')
    return state


def _bottom_state(state,query):
    return replace(state,looked=state.remaining(query),stage='bottom')


def prepare_ripple(effect,context):
    actor=context.actor
    if actor!=context.stack_controller:raise SemanticChoiceError('Ripple requires its resolving controller')
    if actor not in context.query.active_seats:
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason='The Ripple controller is no longer in the game.'))
    try:
        if RIPPLE_STATE_FIELD not in effect:
            spec,_=decode_ripple_instruction(effect,actor=actor,allow_symbolic=False)
            refs=context.query.library_refs(actor,top_first=True)
            if spec.count==0 or not refs:
                return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
                    auto_continue=AutoContinue(reason='There are no cards for Ripple to reveal.'))
            return SemanticChoicePreparation(request=SemanticChoiceRequest(
                prompt=f'Reveal the top {min(spec.count,len(refs))} cards of your library for Ripple?',
                choice=ScalarChoice('choice',legal_values=('reveal','decline')),
                public_context=FrozenMap({'stack':context.stack_ref,'operation':'choose_objects'})),
                continuation_effect=FrozenMap(effect))
        state=_state(effect,actor,context.stack_ref)
        if state.stage=='bottom':return _prepare_bottom(state,context)
        remaining=state.remaining(context.query);legal={i.ref for i in remaining}
        options=tuple(context.query.authorized_cast_options())
        if any(row.get('card') not in legal for row in options):
            raise ValueError('A Ripple casting option is outside its original revealed set')
        if not options:
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='No revealed same-name spell can currently be cast.',
                    prepend_effects=(FrozenMap(_bottom_state(state,context.query).effect()),)))
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt='Cast a revealed spell with the same name without paying its mana cost, or finish Ripple?',
            choice=CastOrDeclineChoice(options),
            public_context=FrozenMap({'stack':context.stack_ref,'operation':'choose_objects','cast_options':options})),
            continuation_effect=FrozenMap({**effect,_OPTIONS:discover_options_fingerprint(options)}))
    except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc


def _prepare_bottom(state,context):
    current=_bottom_state(state,context.query);refs=tuple(i.ref for i in current.looked)
    if len(refs)<=1:
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(current.effect()),
            preparation_intents=(RippleBottomIntent(context.actor,context.stack_ref,current,refs),) if refs else (),
            auto_continue=AutoContinue(reason='The remaining revealed cards have no ordering choice.'))
    return SemanticChoicePreparation(request=SemanticChoiceRequest(
        prompt='Choose the remaining revealed cards in bottom-to-top order.',
        choice=ReferenceSetChoice('order',refs,minimum=len(refs),maximum=len(refs)),
        public_context=FrozenMap({'stack':context.stack_ref,'operation':'choose_objects',
            'objects':[{'id':i.ref,'name':context.query.object(i.ref,zones=('library',)).printed_name} for i in current.looked]})),
        continuation_effect=FrozenMap(current.effect()))


def _cast_response(response):
    choice=response.get('choice');nested=response.get('cast')
    flat={k:v for k,v in response.items() if k not in {'action','action_id','choice_schema','choice','cast'}}
    if choice=='decline' and nested is None and not flat:return None
    if choice!='cast' or nested is not None and not isinstance(nested,Mapping) or nested is not None and nested.keys()&flat.keys():
        raise ValueError('Choose one currently issued Ripple cast option or finish')
    return FrozenMap({**(nested or {}),**flat})


def complete_ripple(continuation,response,query):
    saved=continuation.effect;actor=saved.get('player')
    try:
        if actor not in query.active_seats:raise ValueError('Ripple controller left before completing a choice')
        if RIPPLE_STATE_FIELD not in saved:
            spec,source=decode_ripple_instruction(saved,actor=actor,allow_symbolic=False)
            if set(response)-{'action','action_id','choice_schema','choice'} or response.get('choice') not in ('reveal','decline'):
                raise ValueError('Choose whether to reveal for Ripple')
            if response['choice']=='decline':return SemanticChoiceCompletion()
            refs=query.library_refs(actor,top_first=True)[:spec.count]
            identities=[]
            for ref in refs:
                row=query.object(ref,zones=('library',))
                if row is None or row.owner!=actor:raise ValueError('Ripple lost its owned library top')
                identities.append(LibrarySelectionObjectIdentity(row.object_id,row.logical_object_id,row.ref))
            if not identities:return SemanticChoiceCompletion()
            state=RippleResolutionState(spec,source,continuation.stack_ref,tuple(identities),actor=actor)
            return SemanticChoiceCompletion(
                intents=(RevealLibraryCardsIntent(actor,actor,actor,tuple(refs),'Ripple reveal',public=True),),
                prepend_effects=(FrozenMap(state.effect()),))
        raw={k:v for k,v in saved.items() if k!=_OPTIONS}
        state=_state(raw,actor,continuation.stack_ref)
        if state.stage=='bottom':
            if set(response)-{'action','action_id','choice_schema','order'} or not isinstance(response.get('order'),(list,tuple)):
                raise ValueError('Choose exactly the current revealed remainder order')
            current=_bottom_state(state,query)
            return SemanticChoiceCompletion(intents=(RippleBottomIntent(actor,continuation.stack_ref,current,tuple(response['order'])),))
        options=tuple(query.authorized_cast_options())
        if _OPTIONS not in saved or discover_options_fingerprint(options)!=saved[_OPTIONS]:
            raise ValueError('Ripple casting choices changed')
        submitted=_cast_response(response)
        if submitted is None:
            return SemanticChoiceCompletion(prepend_effects=(FrozenMap(_bottom_state(state,query).effect()),))
        intent=ResolveRippleCastIntent(actor,continuation.stack_ref,state,saved[_OPTIONS],submitted)
        return SemanticChoiceCompletion(intents=(intent,),repeat_effect=FrozenMap(state.effect()))
    except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
