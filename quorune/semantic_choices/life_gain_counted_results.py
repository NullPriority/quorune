"""Validate the sealed event result before delegating the unchanged v2 payment."""
from dataclasses import replace

from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..replacement.immutable import FrozenMap
from ..rules.life_gain_counted_results import (
    capped_life_gain_draw_limit, capped_life_gain_draw_program_dependencies,
    life_gain_counter_payment_projection, life_gain_life_draw_amount, life_gain_life_draw_program_dependencies,
    uses_capped_life_gain_draw, uses_life_gain_life_draw,
)
from ..semantic_runtime.intents import PayManaCostIntent
from .fixed_effect_payment import prepare_fixed_effect_payment
from .model import (
    ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


def prepare_life_gain_counter_payment(effect, context):
    try:
        projected = life_gain_counter_payment_projection(effect, resolved=True)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if context.actor != context.stack_controller or projected['player'] != context.actor:
        raise SemanticChoiceError('Life-gain counter payment lost its ability controller')
    return prepare_fixed_effect_payment(projected, context)


def bind_capped_life_gain_draw(host, item, effect):
    if not uses_capped_life_gain_draw(effect):
        return effect
    payer = item.controller
    amount = capped_life_gain_draw_limit(effect, payer=payer)
    program = host.semantics.get(item.semantic_key)
    if (program is None or not capped_life_gain_draw_program_dependencies(program)
            or item.kind != "triggered_ability" or type(item.context.get("amount")) is not int
            or amount != item.context["amount"]):
        raise ValueError("Capped payment lost its original life-gain occurrence")

    # Generic affordability is monotonic. Keep the quote compact even for large gains.
    lower, upper = 0, amount
    while lower < upper:
        candidate = (lower + upper + 1) // 2
        if host._cost_is_affordable(payer, {"GENERIC": candidate}):
            lower = candidate
        else:
            upper = candidate - 1
    return {**dict(effect), "_requirements": {"GENERIC": lower}}


def _quoted_life_gain_draw(effect, payer):
    try:
        limit = capped_life_gain_draw_limit({key: effect[key] for key in effect if key != "_requirements"},
                                          payer=payer)
        price = effect["_requirements"]
        if set(price) != {"GENERIC"} or type(price["GENERIC"]) is not int or not 0 <= price["GENERIC"] <= limit:
            raise ValueError("Capped payment has an invalid mana quote")
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return limit, price["GENERIC"]


def prepare_capped_life_gain_draw(effect, context):
    if context.actor != context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError("Capped life-gain payment lost its resolving controller")
    limit, payable = _quoted_life_gain_draw(effect, context.actor)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"Pay up to {limit} mana to draw that many cards, or choose 0 to decline.",
            choice=ScalarChoice("x", value_type="integer", minimum=0, maximum=payable),
            public_context=FrozenMap({"stack": context.stack_ref, "payment_kind": "mana", "payable": payable > 0}),
        ),
        continuation_effect=FrozenMap(effect),
    )


def complete_capped_life_gain_draw(continuation, response, query):
    payer = continuation.semantic_frame.controller
    if payer not in query.active_seats:
        raise SemanticChoiceError("Capped life-gain payment lost its resolving controller")
    _, payable = _quoted_life_gain_draw(continuation.effect, payer)
    chosen = response.get("x")
    if type(chosen) is not int or not 0 <= chosen <= payable:
        raise SemanticChoiceError("Choose an integer mana payment within the offered range")
    if not chosen:
        return SemanticChoiceCompletion()
    requirements = FrozenMap({"GENERIC": chosen})
    return SemanticChoiceCompletion(
        intents=(PayManaCostIntent(
            actor=payer, player=payer, requirements=requirements, reason=continuation.note,
            event_code="optional_cost.paid", message=f"{payer} paid {chosen} mana.",
            details=FrozenMap({"cost": requirements, "stack": continuation.stack_ref}),
        ),),
        prepend_effects=(FrozenMap({"op": "draw", "player": payer, "count": chosen, "private": True}),),
    )


def bind_life_gain_life_draw(host, item, effect):
    if not uses_life_gain_life_draw(effect):
        return
    amount = life_gain_life_draw_amount(effect, item.controller)
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not life_gain_life_draw_program_dependencies(program) or item.kind != "triggered_ability"
            or item.context.get("player") != item.controller
            or type(item.context.get("amount")) is not int or item.context["amount"] != amount):
        raise ValueError("Life payment lost its original committed gain occurrence")


def prepare_life_gain_life_draw(effect, context):
    payer = context.actor
    if payer != context.stack_controller:
        raise SemanticChoiceError("Life-gain payment lost its resolving controller")
    try:
        amount = life_gain_life_draw_amount(effect, payer)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc

    payment = FixedEffectPaymentSpec("life", amount=amount)
    projected = {
        "op": effect["op"], "schema_version": 2, "player": payer,
        "payment": payment.to_dict(),
        "effects": [{"op": "draw", "player": payer, "count": amount, "private": True}],
    }
    prepared = prepare_fixed_effect_payment(projected, context)
    request = replace(prepared.request, prompt=f"Pay {amount} life to draw {amount} card(s), or decline.",
                      public_context=FrozenMap({**dict(prepared.request.public_context), "life_payment": amount}))
    return replace(prepared, request=request)
