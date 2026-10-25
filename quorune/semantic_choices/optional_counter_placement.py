from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap, freeze_value
from ..semantic_runtime import SemanticNodeError, default_semantic_interpreter
from ..semantic_runtime.intents import IntentPlan
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_OPERATION = "offer_optional_counter_placement"
_COUNTER_OPERATIONS = frozenset(
    {
        "place_counter_batch",
        "place_counters",
        "place_counters_on_set",
        "place_counters_on_targets",
        "place_player_counters",
    }
)


def _validated_effect(
    effect: Mapping[str, Any],
    *,
    actor: str,
    query: SemanticChoiceQuery,
) -> tuple[str, FrozenMap, IntentPlan]:
    if not isinstance(effect, Mapping) or set(effect) != {
        "op",
        "player",
        "effect",
    }:
        raise SemanticChoiceError(
            "Optional counter placement fields are malformed"
        )
    if effect.get("op") != _OPERATION:
        raise SemanticChoiceError(
            "Optional counter placement operation is invalid"
        )
    player = effect.get("player")
    if (
        type(player) is not str
        or player != actor
        or player not in query.active_seats
    ):
        raise SemanticChoiceError(
            "Optional counter placement must be issued to its active controller"
        )
    counter_effect = effect.get("effect")
    if (
        not isinstance(counter_effect, Mapping)
        or counter_effect.get("op") not in _COUNTER_OPERATIONS
    ):
        raise SemanticChoiceError(
            "Optional counter placement requires one represented counter effect"
        )
    try:
        plan = default_semantic_interpreter().lower_for_seats(
            counter_effect,
            actor=player,
            default_reason="Optional counter placement",
            seats=query.seats,
            active_seats=query.active_seats,
            apnap_order=query.active_seats,
        )
    except SemanticNodeError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if plan is None or plan.operation not in _COUNTER_OPERATIONS:
        raise SemanticChoiceError(
            "Optional counter placement effect is not represented"
        )
    frozen = freeze_value(counter_effect)
    if not isinstance(frozen, FrozenMap):
        raise SemanticChoiceError(
            "Optional counter placement continuation is malformed"
        )
    return player, frozen, plan


@dataclass(frozen=True, slots=True)
class OptionalCounterPlacementHandler:
    """Resolve one controller choice before an ordinary counter transaction."""

    operation: str = _OPERATION
    handler_id: str = "choice.counter.optional-placement.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 603.3",
        "CR 608.2c",
        "CR 608.2d",
        "CR 609.1",
        "CR 122.1",
        "CR 122.6",
        "CR 614.16",
        "CR 616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.optional_fixed_event_trigger",
    )
    continuation_fields: tuple[str, ...] = ("player", "effect")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "PlaceCountersIntent",
        "counter_placement.prepare_counter_placements",
    )
    replay_fixture: str = "optional-fixed-counter-event-trigger"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_counter_event_triggers",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        player, counter_effect, plan = _validated_effect(
            effect,
            actor=context.actor,
            query=context.query,
        )
        from .counter_permission_choices import optional_counter_placement_possible
        continuation = FrozenMap({'op': self.operation, 'player': player, 'effect': counter_effect})
        if not optional_counter_placement_possible(plan, context.query):
            return SemanticChoicePreparation(request=None, continuation_effect=continuation,
                auto_continue=AutoContinue('The optional counter placement is impossible'))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Put the counters?",
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=("put", "decline"),
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
                    "effect": counter_effect,
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
                "Optional counter placement continuation chooser is malformed"
            )
        _player, counter_effect, plan = _validated_effect(
            continuation.effect,
            actor=actor,
            query=query,
        )
        choice = response.get("choice")
        if type(choice) is not str or choice not in {"put", "decline"}:
            raise SemanticChoiceError("Choose put or decline")
        if choice == "decline":
            return SemanticChoiceCompletion()
        from .counter_permission_choices import optional_counter_placement_possible
        if not optional_counter_placement_possible(plan, query):
            return SemanticChoiceCompletion()
        return SemanticChoiceCompletion(prepend_effects=(counter_effect,))


OPTIONAL_COUNTER_PLACEMENT_CHOICE_HANDLERS = (
    OptionalCounterPlacementHandler(),
)


__all__ = [
    "OPTIONAL_COUNTER_PLACEMENT_CHOICE_HANDLERS",
    "OptionalCounterPlacementHandler",
]
