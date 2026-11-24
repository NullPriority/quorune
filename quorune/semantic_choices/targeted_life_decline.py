"""Bind a fixed life alternative to its actual target and resolving controller."""

from dataclasses import replace

from ..replacement.immutable import FrozenMap
from ..rules.targeted_life_decline import targeted_life_decline_parts
from .model import SemanticChoiceCompletion, SemanticChoiceError

_LOCAL = {"_choice_actor", "_stack_label", "_legal_objects"}


def _choice_parts(effect, *, actor, controller, stack_ref, query, resumed=False):
    if resumed and (not _LOCAL.issubset(effect) or effect["_choice_actor"] != actor
            or not isinstance(effect["_legal_objects"], (list, tuple)) or effect["_legal_objects"]):
        raise SemanticChoiceError("Fixed life decline continuation has an invalid fixed-cost payload")
    raw = {key: value for key, value in effect.items() if not resumed or key not in _LOCAL}
    try:
        cost, result = targeted_life_decline_parts(raw)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    item = query.stack_object(stack_ref)
    if (item is None or item.controller != controller or item.targets != (actor,)
            or raw["player"] != actor or actor not in query.active_seats
            or result["actor"] != controller or resumed and effect["_stack_label"] != item.label):
        raise SemanticChoiceError("Fixed life decline payer or resolving authority changed")
    return cost, result


def prepare_targeted_life_decline(effect, context):
    from .fixed_effect_payment import prepare_fixed_payment_cost

    cost, _ = _choice_parts(
        effect, actor=context.actor, controller=context.stack_controller,
        stack_ref=context.stack_ref, query=context.query,
    )
    prepared = prepare_fixed_payment_cost(cost, effect, context)
    request = replace(
        prepared.request, prompt=f"Pay {cost.amount} life to avoid discarding your hand.",
        public_context=FrozenMap({**dict(prepared.request.public_context), "life_cost": cost.amount}),
    )
    return replace(prepared, request=request)


def complete_targeted_life_decline(continuation, response, query):
    from .fixed_effect_payment import complete_fixed_payment_cost

    effect = continuation.effect
    cost, fallback = _choice_parts(
        effect, actor=effect.get("_choice_actor"), controller=continuation.semantic_frame.controller,
        stack_ref=continuation.stack_ref, query=query, resumed=True,
    )
    paid = complete_fixed_payment_cost(cost, effect, response, query)
    return paid if paid.intents else SemanticChoiceCompletion(prepend_effects=(FrozenMap(fallback),))
