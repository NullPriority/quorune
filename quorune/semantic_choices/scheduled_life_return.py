"""Authorize the fixed price and default return from the same upkeep occurrence."""

from dataclasses import replace

from ..replacement.immutable import FrozenMap
from ..rules.scheduled_life_return import scheduled_life_return_parts
from .model import AutoContinue, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation

_LOCAL = {"_choice_actor", "_stack_label", "_legal_objects"}


def _upkeep_choice(effect, *, actor, controller, stack_ref, query, resumed=False):
    item = query.stack_object(stack_ref)
    if (item is None or item.controller != controller or item.targets
            or item.upkeep_player != actor or actor not in query.seats):
        raise SemanticChoiceError("The upkeep life choice lost its original participant")
    if resumed and (not _LOCAL.issubset(effect) or effect["_choice_actor"] != actor or effect["_stack_label"] != item.label
            or not isinstance(effect["_legal_objects"], (list, tuple)) or effect["_legal_objects"]):
        raise SemanticChoiceError("The upkeep life continuation has invalid fixed-cost data")
    raw = {key: value for key,value in effect.items() if not resumed or key not in _LOCAL}
    try:
        return scheduled_life_return_parts(raw, controller=controller, payer=actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def prepare_scheduled_life_return(effect, context):
    from .fixed_effect_payment import prepare_fixed_payment_cost

    actor = context.actor
    cost, _ = _upkeep_choice(effect, actor=actor, controller=context.stack_controller,
        stack_ref=context.stack_ref, query=context.query)
    if actor not in context.query.active_seats:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The upkeep participant has left the game."),
        )
    prepared = prepare_fixed_payment_cost(cost, effect, context)
    request = replace(
        prepared.request, prompt=f"Pay {cost.amount} life to keep your permanent.",
        public_context=FrozenMap({**dict(prepared.request.public_context), "life_cost": cost.amount}),
    )
    return replace(prepared, request=request)


def complete_scheduled_life_return(continuation, response, query):
    from .fixed_effect_payment import complete_fixed_payment_cost

    effect = continuation.effect
    actor = effect.get("_choice_actor")
    cost, result = _upkeep_choice(effect, actor=actor, controller=continuation.semantic_frame.controller,
        stack_ref=continuation.stack_ref, query=query, resumed=True)
    if actor not in query.active_seats:
        return SemanticChoiceCompletion()
    paid = complete_fixed_payment_cost(cost, effect, response, query)
    return paid if paid.intents else SemanticChoiceCompletion(prepend_effects=(FrozenMap(result),))
