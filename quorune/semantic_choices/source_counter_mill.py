"""Offer a targeted opponent the original controller's draw or Mill branch."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..rules.source_counter_mill_choice import OFFER_FIELD, declined_mill_effect
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _offer_subjects(effect, stack, controller, seats):
    marker = effect.get(OFFER_FIELD)
    opponent = effect.get("player")
    if (set(effect) != {"op", "player", "drawer", "count", "private", OFFER_FIELD}
            or not isinstance(marker, Mapping) or set(marker) != {"schema_version", "stack"}
            or type(marker["schema_version"]) is not int or marker["schema_version"] != 1 or marker["stack"] != stack
            or effect["op"] != "offer_draw" or effect["drawer"] != controller or effect["private"] is not True
            or type(effect["count"]) is not int or effect["count"] != 1 or opponent not in seats
            or opponent == controller):
        raise SemanticChoiceError("The source-counter Mill choice lost its original drawer and opponent")
    return opponent


def prepare_counter_mill_offer(effect, context):
    opponent = _offer_subjects(effect, context.stack_ref, context.stack_controller, context.query.active_seats)
    if opponent != context.actor:
        raise SemanticChoiceError("Only the targeted opponent can choose the controller's draw")
    if not context.query.draw_permission(context.stack_controller).allows_complete_draw(1):
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The controller cannot draw", (FrozenMap(
                declined_mill_effect(context.stack_controller, opponent, context.stack_ref)),)),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"Have {context.stack_controller} draw a card?",
            choice=ScalarChoice("choice", ("draw", "decline")),
            public_context=FrozenMap({"operation": "offer_draw", "stack": context.stack_ref,
                                      "drawer": context.stack_controller, "count": 1}),
        ),
        continuation_effect=FrozenMap(effect),
    )


def complete_counter_mill_offer(continuation, response, query):
    controller = continuation.semantic_frame.controller
    stack = continuation.stack_ref
    opponent = _offer_subjects(continuation.effect, stack, controller, query.active_seats)
    choice = response.get("choice")
    if (set(response) - {"choice", "action", "action_id", "choice_schema"} or choice not in ("draw", "decline")
            or not query.draw_permission(controller).allows_complete_draw(1)):
        raise SemanticChoiceError("The targeted opponent must choose from the issued draw options")
    effect = ({"op": "draw", "player": controller, "count": 1, "private": True} if choice == "draw"
              else declined_mill_effect(controller, opponent, stack))
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(effect),))
