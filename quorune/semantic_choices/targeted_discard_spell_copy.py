from __future__ import annotations

"""Offer the independent copy choice to the affected player of a resolving spell."""

from ..replacement.immutable import FrozenMap
from ..rules.targeted_discard_spell_copy import accepted_copy_instruction, is_copy_offer
from .model import (
    ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _validate_offer(effect, *, actor, stack_ref, query):
    source = query.stack_object(stack_ref)
    if (not is_copy_offer(effect, player=actor) or actor not in query.active_seats
            or source is None or source.targets != (actor,)):
        raise SemanticChoiceError("Only the original affected player may copy the resolving spell")


def prepare_targeted_discard_copy(effect, context):
    _validate_offer(effect, actor=context.actor, stack_ref=context.stack_ref, query=context.query)
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
    actor = effect.get("player")
    _validate_offer(effect, actor=actor, stack_ref=continuation.stack_ref, query=query)
    choice = response.get("choice")
    if choice == "decline":
        return SemanticChoiceCompletion()
    if choice != "copy":
        raise SemanticChoiceError("Choose copy or decline")
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(
        accepted_copy_instruction(actor, continuation.stack_ref),
    ),))
