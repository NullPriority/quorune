"""Lower one duration-bound rule through its typed declaration payment intent."""

from dataclasses import dataclass
from ..protected_attack_payment_model import (
    CAPABILITY, OPERATION, TemporaryProtectedAttackPaymentIntent, decode_protected_attack_payment,
)
from ..replacement.immutable import FrozenMap, thaw_value
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class ProtectedAttackPaymentHandler:
    operation: str = OPERATION
    handler_id: str = "generic.protected-attack-payment.v1"
    schema_version: int = 1
    family: str = CAPABILITY
    rule_references: tuple[str, ...] = ("508.1c", "508.1d", "508.1h", "611.2a")
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)

    def lower(self, effect, context):
        if context.source is None:
            raise SemanticNodeError("Protected attack payment needs its original resolving source")
        try:
            cost = decode_protected_attack_payment(thaw_value(FrozenMap(effect)))
            intent = TemporaryProtectedAttackPaymentIntent(context.actor, context.source.stack_ref, cost)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
