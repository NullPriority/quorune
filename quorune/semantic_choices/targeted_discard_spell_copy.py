from __future__ import annotations

"""Offer the independent copy choice to the affected player of a resolving spell."""

from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..rules.targeted_return_spell_copy import (
    LAND_PAYMENT_FIELD, RETURN_COPY_VERSION, RETURN_TARGET_FIELD, return_copy_control_is_bound,
    return_copy_instruction,
)
from .fixed_effect_payment import complete_fixed_payment_cost, prepare_fixed_payment_cost
from ..rules.targeted_discard_spell_copy import COPY_OFFER_FIELD, accepted_copy_instruction, is_copy_offer
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_PAID_LAND_FIELD = "_paid_land"


def _validate_offer(effect, *, actor, stack_ref, query):
    source = query.stack_object(stack_ref)
    if effect.get(COPY_OFFER_FIELD) == RETURN_COPY_VERSION:
        if (type(effect[COPY_OFFER_FIELD]) is not int
                or set(effect) != {"op", "player", COPY_OFFER_FIELD, RETURN_TARGET_FIELD, _PAID_LAND_FIELD}
                or effect.get("op") != "choose_objects" or actor not in query.active_seats
                or not return_copy_control_is_bound(effect, source, actor)):
            raise SemanticChoiceError("The copy choice lost the returned permanent's paying controller")
        payment = effect[_PAID_LAND_FIELD]
        if not isinstance(payment, (list, tuple)) or len(payment) != 2 or any(
            type(value) is not str or not value for value in payment
        ):
            raise SemanticChoiceError("The copy offer lost the original paid land")
        current = query.object(payment[0], zones=("battlefield",))
        return current is None or current.logical_object_id != payment[1]
    if (not is_copy_offer(effect, player=actor) or actor not in query.active_seats
            or source is None or source.targets != (actor,)):
        raise SemanticChoiceError("Only the original affected player may copy the resolving spell")
    return True


def prepare_targeted_discard_copy(effect, context):
    if LAND_PAYMENT_FIELD in effect:
        return prepare_return_controller_land_payment(effect, context)
    if not _validate_offer(effect, actor=context.actor, stack_ref=context.stack_ref, query=context.query):
        return SemanticChoicePreparation(None, FrozenMap(effect),
            auto_continue=AutoContinue("The selected land was not sacrificed"))
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Copy this spell?",
            choice=ScalarChoice(field_name="choice", legal_values=("copy", "decline")),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "choose_objects"}),
        ),
        continuation_effect=FrozenMap(effect),
    )


def complete_targeted_discard_copy(continuation, response, query):
    effect = continuation.effect
    if LAND_PAYMENT_FIELD in effect:
        return complete_return_controller_land_payment(continuation, response, query)
    actor = effect.get("player")
    if not _validate_offer(effect, actor=actor, stack_ref=continuation.stack_ref, query=query):
        raise SemanticChoiceError("The copy no longer has its committed land sacrifice")
    choice = response.get("choice")
    if choice == "decline":
        return SemanticChoiceCompletion()
    if choice != "copy":
        raise SemanticChoiceError("Choose copy or decline")
    instruction = (return_copy_instruction(actor, continuation.stack_ref, effect[RETURN_TARGET_FIELD])
                   if effect[COPY_OFFER_FIELD] == RETURN_COPY_VERSION
                   else accepted_copy_instruction(actor, continuation.stack_ref))
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(instruction),))


def _land_payment(actor):
    return FixedEffectPaymentSpec(
        "sacrifice", predicate=ObjectQuerySpec(zones=("battlefield",), controller=actor, types_all=("land",)),
    )


def prepare_return_controller_land_payment(effect, context):
    actor = context.actor
    if set(effect) != {"op", "player", LAND_PAYMENT_FIELD} or (
        type(effect[LAND_PAYMENT_FIELD]) is not int or effect[LAND_PAYMENT_FIELD] != 1
    ):
        raise SemanticChoiceError("A returned permanent's payment has a closed instruction shape")
    source = context.query.stack_object(context.stack_ref)
    if source is None or len(source.targets) != 1 or not source.targets[0] or effect["player"] != actor:
        raise SemanticChoiceError("The land payment lost its resolving target")
    if actor not in context.query.active_seats:
        return SemanticChoicePreparation(None, FrozenMap(effect),
            auto_continue=AutoContinue("The returned permanent's controller left the game"))
    prepared = prepare_fixed_payment_cost(
        _land_payment(actor), {**effect, RETURN_TARGET_FIELD: source.targets[0]}, context,
    )
    if not prepared.request.public_context["payable"]:
        return SemanticChoicePreparation(None, FrozenMap(effect),
            auto_continue=AutoContinue("The returned permanent's controller has no eligible land"))
    return prepared


def complete_return_controller_land_payment(continuation, response, query):
    effect = continuation.effect
    actor = effect.get("_choice_actor")
    source = query.stack_object(continuation.stack_ref)
    if (actor not in query.active_seats
            or not return_copy_control_is_bound(effect, source, actor)):
        raise SemanticChoiceError("The payment no longer matches its returned permanent and controller")
    paid = complete_fixed_payment_cost(_land_payment(actor), effect, response, query)
    if not paid.intents:
        return paid
    ref, = response["cards"]
    original = next(row["logical_object_id"] for row in effect["_legal_objects"] if row["ref"] == ref)
    copy_offer = {"op": "choose_objects", "player": actor, COPY_OFFER_FIELD: RETURN_COPY_VERSION,
                  RETURN_TARGET_FIELD: effect[RETURN_TARGET_FIELD], _PAID_LAND_FIELD: (ref, original)}
    return SemanticChoiceCompletion(intents=paid.intents, prepend_effects=(FrozenMap(copy_offer),))
