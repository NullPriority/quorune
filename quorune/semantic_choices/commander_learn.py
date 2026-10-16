from __future__ import annotations

"""Commander Learn delegates its optional discard to the existing payment owner."""

from ..commander_learn_model import (
    COMMANDER_LEARN_RULES_PROFILES, validate_commander_learn_instruction,
)
from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .fixed_effect_payment import prepare_fixed_payment_cost, complete_fixed_payment_cost
from .model import SemanticChoiceCompletion, SemanticChoiceError


_CONTINUATION_FIELDS = frozenset({'_choice_actor', '_legal_objects', '_stack_label'})


def _payment(effect, actor, query, *, continuation=False):
    if continuation and not _CONTINUATION_FIELDS.issubset(effect):
        raise SemanticChoiceError('Learn continuation is incomplete')
    instruction = {key: value for key, value in effect.items()
        if not continuation or key not in _CONTINUATION_FIELDS}
    try:
        validate_commander_learn_instruction(instruction, player=actor)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if query.rules_profile not in COMMANDER_LEARN_RULES_PROFILES or actor not in query.active_seats:
        raise SemanticChoiceError('Learn requires the Commander rules profile and an active controller')
    return FixedEffectPaymentSpec('discard', predicate=ObjectQuerySpec(
        zones=('hand',), owner=actor, known_to_actor=True))


def prepare_commander_learn(effect, context):
    if context.actor != context.stack_controller:
        raise SemanticChoiceError('Learn must use the locked resolving controller')
    payment = _payment(effect, context.actor, context.query)
    return prepare_fixed_payment_cost(payment, effect, context)


def complete_commander_learn(continuation, response, query):
    effect = continuation.effect
    actor = effect.get('_choice_actor')
    payment = _payment(effect, actor, query, continuation=True)
    completion = complete_fixed_payment_cost(payment, effect, response, query)
    return SemanticChoiceCompletion(intents=completion.intents,
        prepend_effects=(FrozenMap({'op': 'draw', 'player': actor, 'count': 1, 'private': True}),)
        if completion.intents else ())
