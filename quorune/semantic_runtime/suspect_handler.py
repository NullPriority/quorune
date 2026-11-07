from dataclasses import dataclass

from ..suspect_model import SUSPECT_CAPABILITY, SUSPECT_OPERATION, SuspectIntent, suspect_instruction
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class SuspectHandler:
    operation: str = SUSPECT_OPERATION
    handler_id: str = "generic.suspect-permanent.v1"
    schema_version: int = 1
    family: str = SUSPECT_CAPABILITY
    rule_references: tuple[str, ...] = ("701.60a", "701.60b", "701.60c", "701.60d")
    capability_dependencies: tuple[str, ...] = (SUSPECT_CAPABILITY,)

    def lower(self, effect, context):
        if context.source is None:
            raise SemanticNodeError("Suspect requires a resolving source")
        try:
            clear_creatures, card_ref = suspect_instruction(effect)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        actor = context.query.require_active_seat(context.actor)
        return IntentPlan(self.operation, self.handler_id, (SuspectIntent(actor, card_ref, clear_creatures),))
