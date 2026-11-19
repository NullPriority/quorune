from __future__ import annotations

"""A principal-private choice to cast one current hand spell or decline."""

from collections.abc import Mapping
from ..bounded_hand_cast_model import BoundedHandCastSpec, ResolveBoundedHandCastIntent
from ..discover_model import discover_options_fingerprint
from ..replacement.immutable import FrozenMap
from .model import (
    AutoContinue, CastOrDeclineChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def prepare_bounded_hand_cast(effect, context):
    if context.actor != context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError('The hand free cast requires its original resolving controller')
    try:
        BoundedHandCastSpec.from_effect(effect, actor=context.actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    options = tuple(context.query.authorized_cast_options())
    if not options:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason='The current hand offers no legal free cast.'))
    return SemanticChoicePreparation(request=SemanticChoiceRequest(
        prompt='Cast one eligible spell from your hand without paying its mana cost?',
        choice=CastOrDeclineChoice(options), public_context=FrozenMap({
            'stack': context.stack_ref, 'operation': effect['op'], 'cast_options': options})),
        continuation_effect=FrozenMap({**effect, '_options_fingerprint': discover_options_fingerprint(options)}))


def _submitted_cast(response):
    choice = response.get('choice')
    nested = response.get('cast')
    flat = {key: value for key, value in response.items()
            if key not in {'action', 'action_id', 'cast', 'choice', 'choice_schema'}}
    if choice == 'decline' and nested is None and not flat:
        return None
    if (choice != 'cast' or nested is not None and not isinstance(nested, Mapping)
            or nested is not None and nested.keys() & flat.keys()):
        raise SemanticChoiceError('Choose one current hand free cast or decline')
    return FrozenMap({**(nested or {}), **flat})


def complete_bounded_hand_cast(continuation, response, query):
    effect = continuation.effect
    raw = {key: value for key, value in effect.items() if key != '_options_fingerprint'}
    try:
        actor = effect['player']
        spec = BoundedHandCastSpec.from_effect(raw, actor=actor)
        options = tuple(query.authorized_cast_options())
        fingerprint = effect['_options_fingerprint']
        if actor not in query.active_seats or not options or discover_options_fingerprint(options) != fingerprint:
            raise ValueError('The hand free cast options changed')
        intent = ResolveBoundedHandCastIntent(actor, continuation.stack_ref, spec, fingerprint, _submitted_cast(response))
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(intents=(intent,))
