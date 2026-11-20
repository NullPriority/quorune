"""Quote the sealed caster and resume the ordinary sacrifice only on decline."""

from ..replacement.immutable import FrozenMap
from ..spell_caster_sacrifice_payment import spell_caster_sacrifice_payment_spec
from .fixed_effect_payment import complete_fixed_payment_cost, prepare_fixed_payment_cost
from .model import AutoContinue, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation


def _declined(effect):
    return tuple(FrozenMap(result) for result in effect["declined_effects"])


def prepare_spell_caster_sacrifice_payment(effect, context):
    try:
        cost = spell_caster_sacrifice_payment_spec(
            effect, player=context.actor, controller=context.stack_controller,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if context.actor not in context.query.active_seats:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The caster has left the game."),
        )
    prepared = prepare_fixed_payment_cost(cost, effect, context)
    if prepared.request.public_context["payable"]:
        return prepared
    return SemanticChoicePreparation(
        request=None, continuation_effect=prepared.continuation_effect,
        auto_continue=AutoContinue("The caster has no payable mana plan.", prepend_effects=_declined(effect)),
    )


def complete_spell_caster_sacrifice_payment(continuation, response, query):
    effect = continuation.effect
    caster = effect.get("_choice_actor")
    try:
        cost = spell_caster_sacrifice_payment_spec(
            effect, player=caster, controller=continuation.semantic_frame.controller, continuation=True,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if caster not in query.active_seats:
        raise SemanticChoiceError("The original caster is unavailable")
    paid = complete_fixed_payment_cost(cost, effect, response, query)
    return SemanticChoiceCompletion(
        intents=paid.intents, prepend_effects=() if paid.intents else _declined(effect),
    )
