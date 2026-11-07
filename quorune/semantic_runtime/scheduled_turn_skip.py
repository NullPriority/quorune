from __future__ import annotations

from dataclasses import dataclass

from ..turn_skip_model import (
    TURN_SKIP_CAPABILITY, TURN_SKIP_OPERATION, ScheduleTurnSkipIntent, decode_turn_skip_instruction,
)
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class ScheduledTurnSkipHandler:
    operation: str = TURN_SKIP_OPERATION
    handler_id: str = "generic.scheduled-turn-skip.v1"
    schema_version: int = 1
    family: str = TURN_SKIP_CAPABILITY
    rule_references: tuple[str, ...] = ("500.11", "614.10", "614.10a", "616.1")
    capability_dependencies: tuple[str, ...] = (TURN_SKIP_CAPABILITY,)

    def lower(self, effect, context):
        if context.source is None:
            raise SemanticNodeError("Scheduled skip requires a resolving stack source")
        try:
            spec = decode_turn_skip_instruction(effect)
            actor = context.query.require_active_seat(context.actor)
            players = (
                tuple(seat for seat in context.query.apnap_order if seat != actor)
                if effect.get("players") == "opponents"
                else (context.query.require_known_seat(effect["player"]),)
            )
            intent = ScheduleTurnSkipIntent(
                actor, tuple(seat for seat in players if seat in context.query.active_seats), spec
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
