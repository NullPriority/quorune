"""Exact controller shuffle delegates to the canonical library intent."""
from dataclasses import dataclass

from ..library_order_model import (
    SHUFFLE_OPERATION, SHUFFLE_CAPABILITY, validate_fixed_shuffle_effect,
)
from .context import SemanticNodeError
from .intents import ShuffleLibraryIntent, IntentPlan


@dataclass(frozen=True, slots=True)
class FixedLibraryShuffleHandler:
    operation: str = SHUFFLE_OPERATION
    handler_id: str = 'generic.shuffle-controller-library.v1'
    schema_version: int = 1
    family: str = SHUFFLE_CAPABILITY
    rule_references: tuple[str, ...] = ('401.2', '701.24a')
    capability_dependencies: tuple[str, ...] = (SHUFFLE_CAPABILITY,)

    def lower(self, effect, context):
        try:
            player = validate_fixed_shuffle_effect(effect)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if player != context.actor:
            raise SemanticNodeError('Fixed shuffle requires its resolving controller')
        context.query.require_active_seat(player)
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(ShuffleLibraryIntent(
                actor=context.actor, player=player, reason=context.default_reason,
            ),),
        )
