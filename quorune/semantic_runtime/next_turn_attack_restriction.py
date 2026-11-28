"""Lower the sealed attacking-player result through the declaration owner."""

from dataclasses import dataclass

from ..next_turn_attack_restriction_model import (
    ATTACK_RESTRICTION_CAPABILITY, ATTACK_RESTRICTION_OPERATION, NextTurnAttackRestrictionIntent,
)
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class NextTurnAttackRestrictionHandler:
    operation: str = ATTACK_RESTRICTION_OPERATION
    handler_id: str = "generic.next-turn-player-attack-restriction.v1"
    schema_version: int = 1
    family: str = ATTACK_RESTRICTION_CAPABILITY
    rule_references: tuple[str, ...] = ("508.1c", "611.2a", "611.2c", "614.10a")
    capability_dependencies: tuple[str, ...] = (ATTACK_RESTRICTION_CAPABILITY,)

    def lower(self, effect, context):
        if context.source is None:
            raise SemanticNodeError("A next-turn attack restriction requires its resolving stack item")
        try:
            intent = NextTurnAttackRestrictionIntent.from_effect(
                effect, actor=context.actor, source_id=context.source.stack_ref,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
