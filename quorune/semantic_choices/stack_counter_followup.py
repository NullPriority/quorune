from __future__ import annotations

"""Resume the counter and its bound followups through their ordinary owners."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..rules.stack_counter_followup import (
    STACK_CHARACTERISTIC_FOLLOWUP_VERSION, STACK_COUNTER_FOLLOWUP_CAPABILITY,
    STACK_MANA_VALUE_FOLLOWUP_VERSION, STACK_SHARED_TYPE_HAND_CAST_VERSION,
    STACK_LIBRARY_COUNTER_POSITION_VERSION, bound_library_counter_position, library_position_counter_effect,
    STACK_COUNTER_FOLLOWUP_OPERATION, bound_stack_counter_instructions,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue, SemanticChoiceCompletion, SemanticChoiceContinuation,
    ScalarChoice, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


def _prepare_library_counter_position(effect, context):
    stack, needs_position = bound_library_counter_position(effect)
    local = FrozenMap(effect)
    if not needs_position:
        return SemanticChoicePreparation(
            request=None, continuation_effect=local,
            auto_continue=AutoContinue(reason="No physical spell card can move to the library",
                                       prepend_effects=(FrozenMap(library_position_counter_effect(stack)),)),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Put the countered card on top or bottom of its owner's library?",
            choice=ScalarChoice("position", ("top", "bottom")),
            public_context=FrozenMap({"stack": context.stack_ref, "target": stack}),
        ),
        continuation_effect=local,
    )


@dataclass(frozen=True, slots=True)
class StackCounterFollowupHandler:
    operation: str = STACK_COUNTER_FOLLOWUP_OPERATION
    handler_id: str = "choice.effect.stack-counter-followup.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 608.2b", "CR 608.2c", "CR 608.2h", "CR 701.6a")
    capability_dependencies: tuple[str, ...] = (STACK_COUNTER_FOLLOWUP_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("stack", "recipient", "followups", "schema_version", "result", "mana_value", "card_types", "needs_position")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("AutoContinue.prepend_effects", "CommanderEngine._continue_resolution")
    replay_fixture: str = "fixed-stack-counter-controller-followup"
    test_modules: tuple[str, ...] = ("tests.test_mixed_stack_counter_targets",)

    def prepare(self, effect: Mapping[str, Any], context: SemanticChoiceContext) -> SemanticChoicePreparation:
        if context.actor != context.stack_controller or effect.get("stack") == context.stack_ref:
            raise SemanticChoiceError("Counter followup requires the resolving controller and another stack object")
        try:
            if effect.get("schema_version") == STACK_LIBRARY_COUNTER_POSITION_VERSION:
                return _prepare_library_counter_position(effect, context)
            instructions = bound_stack_counter_instructions(effect, active_seats=context.query.active_seats)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(
                reason={
                    STACK_CHARACTERISTIC_FOLLOWUP_VERSION: "bind the counter target's current characteristics before its ordered followup",
                    STACK_MANA_VALUE_FOLLOWUP_VERSION: "capture the counter target's mana value before its ordered followup",
                    STACK_SHARED_TYPE_HAND_CAST_VERSION: "capture the counter target's card types before the hand-cast offer",
                }.get(effect.get("schema_version"), "bind the counter target controller before its ordered followups"),
                prepend_effects=tuple(FrozenMap(child) for child in instructions),
            ),
        )

    def complete(self, continuation: SemanticChoiceContinuation, response: Mapping[str, Any],
                 query: SemanticChoiceQuery) -> SemanticChoiceCompletion:
        effect = continuation.effect
        if effect.get("schema_version") != STACK_LIBRARY_COUNTER_POSITION_VERSION:
            raise SemanticChoiceError("Counter followups issue no choice at the binding step")
        try:
            ref, needs_position = bound_library_counter_position(effect)
            resolving = query.stack_object(continuation.stack_ref)
            target = query.stack_object(ref)
            if (not needs_position or target is None or resolving is None or target.ref == resolving.ref
                    or resolving.controller != continuation.semantic_frame.controller
                    or resolving.controller not in query.active_seats):
                raise ValueError("The original resolving controller and target are required")
            position = response.get("position")
            if type(position) is not str:
                raise ValueError("The counter destination must be a library position")
            counter = library_position_counter_effect(ref, position=position)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(prepend_effects=(FrozenMap(counter),))


STACK_COUNTER_FOLLOWUP_HANDLERS = (StackCounterFollowupHandler(),)
