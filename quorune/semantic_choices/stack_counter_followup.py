from __future__ import annotations

"""Resume the counter and its bound followups through their ordinary owners."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..rules.stack_counter_followup import (
    STACK_COUNTER_FOLLOWUP_CAPABILITY, STACK_COUNTER_FOLLOWUP_OPERATION, bound_stack_counter_instructions,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation,
)


@dataclass(frozen=True, slots=True)
class StackCounterFollowupHandler:
    operation: str = STACK_COUNTER_FOLLOWUP_OPERATION
    handler_id: str = "choice.effect.stack-counter-followup.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 608.2b", "CR 608.2c", "CR 608.2h", "CR 701.6a")
    capability_dependencies: tuple[str, ...] = (STACK_COUNTER_FOLLOWUP_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("stack", "recipient", "followups", "schema_version")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("AutoContinue.prepend_effects", "CommanderEngine._continue_resolution")
    replay_fixture: str = "fixed-stack-counter-controller-followup"
    test_modules: tuple[str, ...] = ("tests.test_mixed_stack_counter_targets",)

    def prepare(self, effect: Mapping[str, Any], context: SemanticChoiceContext) -> SemanticChoicePreparation:
        if context.actor != context.stack_controller or effect.get("stack") == context.stack_ref:
            raise SemanticChoiceError("Counter followup requires the resolving controller and another stack object")
        try:
            instructions = bound_stack_counter_instructions(effect, active_seats=context.query.active_seats)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(
                reason="bind the counter target controller before its ordered followups",
                prepend_effects=tuple(FrozenMap(child) for child in instructions),
            ),
        )

    def complete(self, continuation: SemanticChoiceContinuation, response: Mapping[str, Any],
                 query: SemanticChoiceQuery) -> SemanticChoiceCompletion:
        raise SemanticChoiceError("Counter followups issue no choice at the binding step")


STACK_COUNTER_FOLLOWUP_HANDLERS = (StackCounterFollowupHandler(),)
