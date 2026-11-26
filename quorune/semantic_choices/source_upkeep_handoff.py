from __future__ import annotations

"""Resolve one retained source handoff through the optional-effect choice."""

from ..continuous_effect_state import ResolutionEffectSource
from ..delayed_object_model import resolved_source_convert_effect
from ..replacement.immutable import FrozenMap
from ..rules.source_upkeep_handoff import decode_source_upkeep_handoff
from ..semantic_runtime.control_intents import UpkeepSourceHandoffIntent
from .model import (
    AutoContinue,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _choice_source(effect, actor, query):
    if actor not in query.active_seats:
        raise SemanticChoiceError("The original resolving controller owns the handoff choice")
    try:
        instruction = decode_source_upkeep_handoff(effect, actor=actor, resolved=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    reference = instruction["card"]
    if reference is None:
        return instruction, None
    context = query.resolution_source
    if context is None or context.card_ref != reference or context.logical_object_id is None:
        raise SemanticChoiceError("The handoff requires its original resolving source")
    current = query.object(reference, zones=("battlefield",))
    if current is None or current.phased_out or current.logical_object_id != context.logical_object_id:
        return instruction, None
    return instruction, ResolutionEffectSource(**context.to_dict())


def _decline_effects(instruction, source):
    if source is None:
        return ()
    return (FrozenMap(resolved_source_convert_effect(
        reference=instruction["card"], logical_object_id=source.logical_object_id,
        transform_count=instruction["expected_transform_count"],
    )),)


def prepare_source_upkeep_handoff(effect, context):
    if context.actor != context.stack_controller:
        raise SemanticChoiceError("The original resolving controller owns the handoff choice")
    instruction, source = _choice_source(effect, context.actor, context.query)
    continuation = FrozenMap(effect)
    if source is None or instruction["recipient"] not in context.query.active_seats:
        return SemanticChoicePreparation(
            request=None, continuation_effect=continuation,
            auto_continue=AutoContinue("The original handoff is unavailable.", _decline_effects(instruction, source)),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Give the active opponent control of this permanent until end of turn?",
            choice=ScalarChoice(field_name="choice", legal_values=("apply", "decline")),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "offer_optional_effect"}),
        ),
        continuation_effect=continuation,
    )


def complete_source_upkeep_handoff(continuation, response, query):
    actor = continuation.semantic_frame.controller
    instruction, source = _choice_source(continuation.effect, actor, query)
    choice = response.get("choice")
    if choice == "decline":
        return SemanticChoiceCompletion(prepend_effects=_decline_effects(instruction, source))
    if choice != "apply":
        raise SemanticChoiceError("Choose apply or decline")
    if source is None or instruction["recipient"] not in query.active_seats:
        raise SemanticChoiceError("The original handoff is unavailable")
    return SemanticChoiceCompletion(intents=(UpkeepSourceHandoffIntent(
        actor=actor, object_ref=instruction["card"], controller=instruction["recipient"], source=source,
        reason="Opponent-upkeep control handoff",
    ),))
