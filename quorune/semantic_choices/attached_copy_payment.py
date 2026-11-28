"""Reuse the registered fixed-mana payment and its original trigger controller."""

from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..replacement.immutable import FrozenMap
from ..rules.attached_copy_payment import (
    VERSION, OPERATION, committed_copy_fallback,
)
from .fixed_effect_payment import complete_fixed_payment_cost, prepare_fixed_payment_cost
from .model import AutoContinue, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation

_FIELDS = {"op", "schema_version", "player", "source", "payment", "cost", "effects", "declined_effects"}
_LOCAL_FIELDS = {"_choice_actor", "_stack_label", "_legal_objects"}


def _bound_parts(effect, actor, *, continuation=False):
    try:
        if (set(effect) != (_FIELDS | _LOCAL_FIELDS if continuation else _FIELDS)
                or effect["op"] != OPERATION or type(effect["schema_version"]) is not int
                or effect["schema_version"] != VERSION or effect["player"] != actor
                or type(actor) is not str or not actor or actor.startswith("$")
                or type(effect["source"]) is not str or not effect["source"] or effect["source"].startswith("$")):
            raise ValueError("Attached copy payment lost its original controller and public model")
        payment = FixedEffectPaymentSpec.from_dict(effect["payment"])
        if payment.kind != "mana" or FrozenMap(effect["cost"]) != FrozenMap(payment.requirements):
            raise ValueError("Attached copy payment lost its fixed ordinary mana cost")
        copy, = effect["effects"]
        fallback, = effect["declined_effects"]
        if FrozenMap(committed_copy_fallback(copy, player=actor)) != FrozenMap(fallback):
            raise ValueError("Attached copy payment changed its fallback")
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return payment, FrozenMap(copy), FrozenMap(fallback)


def prepare_attached_copy_payment(effect, context):
    payment, _, fallback = _bound_parts(effect, context.actor)
    prepared = prepare_fixed_payment_cost(payment, effect, context)
    if not prepared.request.public_context["payable"]:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The attached copy cost cannot be paid", (fallback,)))
    return prepared


def complete_attached_copy_payment(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    if effect.get("_choice_actor") != actor or actor not in query.active_seats:
        raise SemanticChoiceError("Attached copy payment requires the original trigger controller")
    payment, copy, fallback = _bound_parts(effect, actor, continuation=True)
    settled = complete_fixed_payment_cost(payment, effect, response, query)
    return SemanticChoiceCompletion(intents=settled.intents,
        prepend_effects=(copy,) if settled.intents else (fallback,))
