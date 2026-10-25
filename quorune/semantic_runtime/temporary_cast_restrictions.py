from __future__ import annotations

from dataclasses import dataclass
from ..temporary_cast_rule_model import CAPABILITY,OPERATION,TemporaryCastRestrictionSpec,TemporaryCastRestrictionIntent
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True,slots=True)
class TemporaryCastRestrictionHandler:
    operation: str=OPERATION
    handler_id: str='generic.temporary-cast-prohibition.v1'
    schema_version: int=1
    family: str=CAPABILITY
    rule_references: tuple[str,...]=('101.2','601.2e','611.2c','514.2')
    capability_dependencies: tuple[str,...]=(CAPABILITY,)

    def lower(self,effect,context):
        if (set(effect)-{'op','actor','restriction','player','reason'} or not {'op','actor','restriction','player'}<=set(effect)
                or effect['op']!=self.operation or effect['actor']!=context.actor or context.source is None):
            raise SemanticNodeError('Temporary casting restriction requires its owned source context')
        try:intent=TemporaryCastRestrictionIntent(context.actor,TemporaryCastRestrictionSpec.from_dict(effect['restriction']),effect['player'],context.source.stack_ref)
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation,handler_id=self.handler_id,intents=(intent,))
