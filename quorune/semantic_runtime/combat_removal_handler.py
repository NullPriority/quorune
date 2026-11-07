"""Lower one resolved combat-removal instruction into its immutable intent."""

from dataclasses import dataclass

from ..combat_removal import (
    COMBAT_REMOVAL_CAPABILITY, COMBAT_REMOVAL_OPERATION, CombatRemovalIntent, combat_removal_instruction,
)
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class CombatRemovalHandler:
    operation: str = COMBAT_REMOVAL_OPERATION
    handler_id: str = 'generic.remove-from-combat.v1'
    schema_version: int = 1
    family: str = COMBAT_REMOVAL_CAPABILITY
    rule_references: tuple[str, ...] = ('506.4', '509.1h', '608.2c')
    capability_dependencies: tuple[str, ...] = (COMBAT_REMOVAL_CAPABILITY,)

    def lower(self, effect, context):
        try:
            object_ref, untap = combat_removal_instruction(effect)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        actor = context.query.require_active_seat(context.actor)
        return IntentPlan(self.operation, self.handler_id, (
            CombatRemovalIntent(actor, object_ref, untap, context.default_reason),
        ))
