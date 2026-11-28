"""Private repeat choice after one committed reveal, move and life-loss stage."""

from ..replacement.immutable import FrozenMap
from ..rules.repeat_top_hand_life import repeat_top_hand_life_effect, validate_repeat_top_hand_life
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def prepare_repeat_top_hand_life(effect, context, prepare_single):
    actor = context.actor
    if actor != context.stack_controller:
        raise SemanticChoiceError("Only the resolving controller can repeat the procedure")
    try:
        stage = validate_repeat_top_hand_life(effect, player=actor, stack_ref=context.stack_ref)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if stage == "iterate":
        next_offer = repeat_top_hand_life_effect(player=actor, stage="offer", stack_ref=context.stack_ref)
        return prepare_single(effect, context, repeat_offer=next_offer)
    if not context.query.library_refs(actor, top_first=True):
        return SemanticChoicePreparation(None, FrozenMap(effect),
            auto_continue=AutoContinue("No additional library card can be revealed"))
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Reveal another card?",
            choice=ScalarChoice("choice", ("repeat", "stop")),
            public_context=FrozenMap({"operation": effect["op"], "stack": context.stack_ref}),
        ),
        continuation_effect=FrozenMap(effect),
    )


def complete_repeat_top_hand_life(continuation, response, query):
    effect, controller, stack_ref = (continuation.effect, continuation.semantic_frame.controller, continuation.stack_ref)
    try:
        stage = validate_repeat_top_hand_life(effect, player=controller, stack_ref=stack_ref)
        if (stage != "offer" or set(response) - {"choice", "action", "action_id", "choice_schema"}
                or response.get("action", "choose") != "choose" or response.get("choice") not in ("repeat", "stop")):
            raise ValueError("Choose whether to repeat the completed procedure")
        if response["choice"] == "stop":
            return SemanticChoiceCompletion()
        if not query.library_refs(controller, top_first=True):
            raise ValueError("There is no remaining library card to reveal")
        next_effect = repeat_top_hand_life_effect(player=controller, stage="iterate", stack_ref=stack_ref)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(repeat_effect=FrozenMap(next_effect))
