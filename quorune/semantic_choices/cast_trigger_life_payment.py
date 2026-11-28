from __future__ import annotations

"""Resolve the original cast trigger's public, sequential life payments."""

from ..replacement.immutable import FrozenMap
from ..rules.cast_trigger_life_payment import CAST_TRIGGER_LIFE_PAYMENT_FIELD, CastTriggerLifePaymentSpec
from ..semantic_runtime.intents import PayLifeIntent
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_BOUND = {"op", CAST_TRIGGER_LIFE_PAYMENT_FIELD, "original_stack"}
_ROUND = _BOUND | {"player", "payer_order", "payer_index", "any_paid"}
_RESPONSE = {"action", "action_id", "a", "cap", "choice", "reason", "plan", "next", "choice_schema", "label"}


def _original(effect):
    original = effect.get("original_stack")
    if (set(effect) not in (_BOUND, _ROUND) or effect.get("op") != "choose_option"
            or type(original) is not str or not original or original.startswith("$")):
        raise SemanticChoiceError("A cast-trigger offer requires its original spell and sealed price")
    try:
        return CastTriggerLifePaymentSpec.from_dict(effect[CAST_TRIGGER_LIFE_PAYMENT_FIELD])
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _round(effect, query):
    order = effect.get("payer_order")
    index = effect.get("payer_index")
    if (not isinstance(order, (list, tuple)) or not order or any(type(seat) is not str for seat in order)
            or len(set(order)) != len(order) or not set(order) <= set(query.seats)
            or tuple(seat for seat in order if seat in query.active_seats) != tuple(query.apnap_order)
            or type(index) is not int or not 0 <= index < len(order)
            or effect.get("player") != order[index] or type(effect.get("any_paid")) is not bool):
        raise SemanticChoiceError("The cast-trigger offer lost its original APNAP round")
    return tuple(order), index


def _remaining_offer(effect, query, *, order, index, any_paid):
    next_index = next((i for i in range(index + 1, len(order)) if order[i] in query.active_seats), None)
    if next_index is None:
        return ()
    return (FrozenMap({
        **effect, "player": order[next_index], "payer_order": order,
        "payer_index": next_index, "any_paid": any_paid,
    }),)


def _payable(life, amount):
    return amount == 0 or life >= amount


def prepare_cast_trigger_life_payment(effect, context):
    spec = _original(effect)
    continuation = FrozenMap(effect)
    if "player" not in effect:
        if context.actor != context.stack_controller:
            raise SemanticChoiceError("Only the cast trigger may start its original payer round")
        return SemanticChoicePreparation(
            request=None, continuation_effect=continuation,
            auto_continue=AutoContinue("Order the cast-trigger life offers",
                _remaining_offer(effect, context.query, order=tuple(context.query.apnap_order), index=-1, any_paid=False)),
        )
    order, index = _round(effect, context.query)
    player = effect["player"]
    if context.actor != player or player not in context.query.active_seats:
        raise SemanticChoiceError("The current player owns this life payment offer")
    life = context.query.player_life(player)
    amount = spec.amount(life)
    if not _payable(context.query.player_life_for_payment(player), amount):
        return SemanticChoicePreparation(
            request=None, continuation_effect=continuation,
            auto_continue=AutoContinue("The current player cannot pay the full life cost",
                _remaining_offer(effect, context.query, order=order, index=index, any_paid=effect["any_paid"])),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"Pay {amount} life?",
            choice=ScalarChoice("choice", ("pay", "decline")),
            public_context=FrozenMap({"stack": context.stack_ref, "player": player, "life_cost": amount}),
        ),
        continuation_effect=continuation,
    )


def complete_cast_trigger_life_payment(continuation, response, query):
    effect = continuation.effect
    spec = _original(effect)
    order, index = _round(effect, query)
    player = effect["player"]
    answer = response.get("choice")
    if set(response) - _RESPONSE or answer not in ("pay", "decline") or player not in query.active_seats:
        raise SemanticChoiceError("Choose pay or decline for the current offered player")
    if answer == "decline":
        return SemanticChoiceCompletion(prepend_effects=_remaining_offer(
            effect, query, order=order, index=index, any_paid=effect["any_paid"],
        ))
    life = query.player_life(player)
    amount = spec.amount(life)
    if not _payable(query.player_life_for_payment(player), amount):
        raise SemanticChoiceError("The current player cannot pay the full life cost")
    original = effect["original_stack"]
    counter = () if effect["any_paid"] or query.stack_object(original) is None else (
        FrozenMap({"op": "counter_stack_target", "stack": original}),
    )
    return SemanticChoiceCompletion(
        intents=(PayLifeIntent(player, player, amount, "Pay life for the original cast trigger"),),
        prepend_effects=(*counter, *_remaining_offer(effect, query, order=order, index=index, any_paid=True)),
    )
