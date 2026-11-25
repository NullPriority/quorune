"""Lower one resolved postcombat phase instruction to the turn owner."""

from dataclasses import dataclass

from ..additional_beginning_model import (
    ADDITIONAL_BEGINNING_CAPABILITY,
    ADDITIONAL_BEGINNING_OPERATION,
    ScheduleAdditionalBeginningIntent,
    decode_additional_beginning_instruction,
)
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class ScheduledAdditionalBeginningHandler:
    operation: str = ADDITIONAL_BEGINNING_OPERATION
    handler_id: str = "generic.additional-beginning-after-postcombat.v1"
    schema_version: int = 1
    family: str = ADDITIONAL_BEGINNING_CAPABILITY
    rule_references: tuple[str, ...] = ("500.8", "502.2", "503.1", "504.1", "603.2")
    capability_dependencies: tuple[str, ...] = (ADDITIONAL_BEGINNING_CAPABILITY,)

    def lower(self, effect, context):
        source = context.source
        if source is None:
            raise SemanticNodeError("Additional beginning requires a resolving stack source")
        try:
            player = decode_additional_beginning_instruction(effect, resolved=True)
            intent = ScheduleAdditionalBeginningIntent(
                context.query.require_active_seat(context.actor),
                context.query.require_known_seat(player),
                source.card_ref or source.stack_ref,
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
