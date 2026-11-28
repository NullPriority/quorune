"""Offer each mandatory opponent choice during the spell's resolution."""

from ..replacement.immutable import FrozenMap
from ..rules.paired_token_creators import paired_token_inputs, paired_token_result
from ..semantic_runtime.intents import RecordChoiceIntent
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)

_LOCAL = frozenset({"_legal_values", "_stack_label"})
_RESPONSE = frozenset({"choice", "choice_schema", "action", "action_id"})


def _opponents(query, actor):
    return tuple(seat for seat in query.active_seats if seat != actor)


def prepare_paired_token_choice(effect, context):
    try:
        actor = context.actor
        if actor != context.stack_controller or actor not in context.query.active_seats:
            raise ValueError("The resolving spell's controller chooses the opponent")
        paired_token_inputs(effect, actor=actor)
    except (AttributeError, TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    legal = _opponents(context.query, actor)
    saved = FrozenMap({**effect, "_legal_values": legal, "_stack_label": context.stack_label})
    if not legal:
        return SemanticChoicePreparation(request=None, continuation_effect=saved,
            auto_continue=AutoContinue(reason="There is no remaining opponent to choose."))
    return SemanticChoicePreparation(request=SemanticChoiceRequest(
        prompt="Choose an opponent to create tokens.",
        choice=ScalarChoice(field_name="choice", legal_values=legal),
        public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"]})),
        continuation_effect=saved)


def complete_paired_token_choice(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    try:
        token, amount, use, _ = paired_token_inputs(
            {key: value for key, value in effect.items() if key not in _LOCAL}, actor=actor,
        )
        legal = _opponents(query, actor)
        if actor not in query.active_seats or tuple(effect["_legal_values"]) != legal or set(response) - _RESPONSE:
            raise ValueError("Paired token choice no longer matches the authorized opponents")
        if not legal and not response.get("choice"):
            return SemanticChoiceCompletion()
        chosen = response.get("choice")
        if type(chosen) is not str or chosen not in legal:
            raise ValueError("Choose one offered opponent")
    except (AttributeError, TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    result = paired_token_result(token, amount, use, chosen)
    return SemanticChoiceCompletion(
        intents=(RecordChoiceIntent(
            actor=actor, event_code="semantic.option.chosen",
            message=f"{actor} chose {chosen} for {effect['_stack_label']}.",
            details=FrozenMap({"stack": continuation.stack_ref, "choice": chosen}),
        ),),
        prepend_effects=tuple(FrozenMap(instruction) for instruction in result),
    )
