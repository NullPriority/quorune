from __future__ import annotations

from dataclasses import dataclass

from ..fixed_token_production import (
    INVESTIGATE_CAPABILITY_ID,
    SEQUENTIAL_INVESTIGATE_OPERATION,
    FixedTokenProductionError,
    clue_token_effect,
    sequential_investigate_count,
)
from ..replacement.immutable import FrozenMap
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


@dataclass(frozen=True, slots=True)
class SequentialInvestigateHandler:
    operation: str = SEQUENTIAL_INVESTIGATE_OPERATION
    handler_id: str = "choice.investigate-sequential.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("608.2c", "614.5", "701.16a")
    capability_dependencies: tuple[str, ...] = (INVESTIGATE_CAPABILITY_ID,)
    continuation_fields: tuple[str, ...] = ("schema_version", "controller", "count")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("CreateTokenIntent", "quorune.token_creation.create_tokens")
    replay_fixture: str = "separate-investigate-token-events"
    test_modules: tuple[str, ...] = ("tests.test_token_creation_triggers",)

    def prepare(self, effect, context):
        try:
            count = sequential_investigate_count(effect, resolved_controller=context.actor)
        except FixedTokenProductionError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if context.actor not in context.query.active_seats:
            raise SemanticChoiceError("The investigating controller is unavailable")
        clue = FrozenMap({**clue_token_effect(1), "controller": context.actor})
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(
                reason="Create a Clue for each Investigate action.",
                prepend_effects=(clue,) * count,
            ),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("Sequential Investigate does not accept player selections")
