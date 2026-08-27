from __future__ import annotations

"""Generic resolution-time choice for one already represented effect clause."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..compiler.optional_effect_templates import OPTIONAL_EFFECT_OPERATION
from ..replacement.immutable import FrozenMap, freeze_value
from ..semantic_runtime import SemanticNodeError, default_semantic_interpreter
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _represented_effect(
    effect: Mapping[str, Any],
    *,
    actor: str,
    query: SemanticChoiceQuery,
) -> None:
    operation = effect.get("op")
    if type(operation) is not str or not operation:
        raise SemanticChoiceError(
            "Optional effect instructions require a named operation"
        )
    if operation == OPTIONAL_EFFECT_OPERATION:
        raise SemanticChoiceError("Optional effects cannot nest")
    try:
        plan = default_semantic_interpreter().lower_for_seats(
            effect,
            actor=actor,
            default_reason="Optional effect",
            seats=query.seats,
            active_seats=query.active_seats,
            apnap_order=query.active_seats,
        )
    except SemanticNodeError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if plan is not None:
        return
    from .defaults import default_semantic_choice_registry

    try:
        default_semantic_choice_registry().handler_for_operation(operation)
    except SemanticChoiceError as exc:
        raise SemanticChoiceError(
            "Optional effect instruction is not represented"
        ) from exc


def _validated_effects(
    effect: Mapping[str, Any],
    *,
    actor: str,
    query: SemanticChoiceQuery,
) -> tuple[str, tuple[FrozenMap, ...]]:
    if not isinstance(effect, Mapping) or set(effect) != {
        "op",
        "player",
        "effects",
    }:
        raise SemanticChoiceError("Optional effect fields are malformed")
    if effect.get("op") != OPTIONAL_EFFECT_OPERATION:
        raise SemanticChoiceError("Optional effect operation is invalid")
    player = effect.get("player")
    if (
        type(player) is not str
        or player != actor
        or player not in query.active_seats
    ):
        raise SemanticChoiceError(
            "Optional effect must be issued to its active controller"
        )
    values = effect.get("effects")
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or len(values) != 1
        or any(not isinstance(value, Mapping) for value in values)
    ):
        raise SemanticChoiceError(
            "Optional effect requires one represented instruction"
        )
    frozen: list[FrozenMap] = []
    for value in values:
        _represented_effect(value, actor=player, query=query)
        candidate = freeze_value(value)
        if not isinstance(candidate, FrozenMap):
            raise SemanticChoiceError(
                "Optional effect continuation is malformed"
            )
        frozen.append(candidate)
    return player, tuple(frozen)


@dataclass(frozen=True, slots=True)
class OptionalEffectHandler:
    """Offer one choice before resuming ordinary typed effect resolution."""

    operation: str = OPTIONAL_EFFECT_OPERATION
    handler_id: str = "choice.effect.optional-fixed.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 109.5",
        "CR 608.2c",
        "CR 608.2d",
        "CR 609.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "effect.choice.optional_fixed",
    )
    continuation_fields: tuple[str, ...] = ("player", "effects")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "SemanticChoiceCompletion.prepend_effects",
        "CommanderEngine._continue_resolution",
    )
    replay_fixture: str = "fixed-optional-effect-choice"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_optional_effect_choices",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        player, effects = _validated_effects(
            effect,
            actor=context.actor,
            query=context.query,
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Apply the optional effect?",
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=("apply", "decline"),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    "op": self.operation,
                    "player": player,
                    "effects": effects,
                }
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        actor = continuation.effect.get("player")
        if type(actor) is not str:
            raise SemanticChoiceError(
                "Optional effect continuation chooser is malformed"
            )
        _player, effects = _validated_effects(
            continuation.effect,
            actor=actor,
            query=query,
        )
        choice = response.get("choice")
        if type(choice) is not str or choice not in {"apply", "decline"}:
            raise SemanticChoiceError("Choose apply or decline")
        return SemanticChoiceCompletion(
            prepend_effects=effects if choice == "apply" else ()
        )


OPTIONAL_EFFECT_CHOICE_HANDLERS = (OptionalEffectHandler(),)


__all__ = ["OPTIONAL_EFFECT_CHOICE_HANDLERS", "OptionalEffectHandler"]
