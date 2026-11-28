"""Lower one sealed set of counter-marked attackers into declaration rules."""

from dataclasses import dataclass

from ..counter_bound_attack_model import (
    COUNTER_BOUND_ATTACK_CAPABILITY, COUNTER_BOUND_ATTACK_OPERATION, CounterBoundAttackRestrictionIntent,
)
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class CounterBoundAttackRestrictionHandler:
    operation: str = COUNTER_BOUND_ATTACK_OPERATION
    handler_id: str = "generic.counter-bound-creature-attack-restriction.v1"
    schema_version: int = 1
    family: str = COUNTER_BOUND_ATTACK_CAPABILITY
    rule_references: tuple[str, ...] = ("122.2", "400.7", "508.1c", "611.2a", "611.2b", "611.2c")
    capability_dependencies: tuple[str, ...] = (COUNTER_BOUND_ATTACK_CAPABILITY,)

    def lower(self, effect, context):
        if context.source is None:
            raise SemanticNodeError("Counter-marked attack restrictions require the originating stack object")
        try:
            intent = CounterBoundAttackRestrictionIntent.from_effect(
                effect, actor=context.actor, source_id=context.source.stack_ref,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
