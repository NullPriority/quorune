from __future__ import annotations

"""Select a represented result without a new choice or mutation path."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..resolution_conditions import (
    RESOLUTION_CONDITION_CAPABILITY,
    RESOLUTION_CONDITION_OPERATION,
    validate_resolution_condition_instruction,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation,
)
from .optional_effect import _represented_effect


@dataclass(frozen=True, slots=True)
class PublicResolutionConditionHandler:
    operation: str = RESOLUTION_CONDITION_OPERATION
    handler_id: str = "choice.effect.public-resolution-condition.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 109.5", "CR 608.2c", "CR 608.2h")
    capability_dependencies: tuple[str, ...] = (RESOLUTION_CONDITION_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "condition", "effects", "mechanic_ids", "prefix_mechanic_ids")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = (
        "AutoContinue.prepend_effects", "CommanderEngine._continue_resolution",
    )
    replay_fixture: str = "fixed-resolution-public-condition"
    test_modules: tuple[str, ...] = ("tests.test_resolution_public_conditions",)

    def prepare(self, effect: Mapping[str, Any], context: SemanticChoiceContext) -> SemanticChoicePreparation:
        try:
            condition, effects = validate_resolution_condition_instruction(effect)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if effect["player"] != context.actor or context.actor != context.stack_controller:
            raise SemanticChoiceError("Resolution conditions require the resolving controller")
        for child in effects:
            _represented_effect(child, actor=context.actor, query=context.query)
        try:
            matched = context.query.resolution_condition_matches(condition.to_dict())
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(
                reason="public resolution condition matched" if matched else "public resolution condition did not match",
                prepend_effects=tuple(FrozenMap(child) for child in effects) if matched else (),
            ),
        )

    def complete(self, continuation: SemanticChoiceContinuation, response: Mapping[str, Any], query: SemanticChoiceQuery) -> SemanticChoiceCompletion:
        raise SemanticChoiceError("Public resolution conditions issue no player choice")


PUBLIC_RESOLUTION_CONDITION_HANDLERS = (PublicResolutionConditionHandler(),)
