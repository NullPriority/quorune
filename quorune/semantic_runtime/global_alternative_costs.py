from __future__ import annotations

from dataclasses import dataclass
from ..rules.global_alternative_cost_model import (
    GLOBAL_ALTERNATIVE_CAPABILITY,GLOBAL_ALTERNATIVE_HANDLER,GLOBAL_ALTERNATIVE_EVENT,GlobalAlternativeCostSpec,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class GlobalAlternativeCostHandler:
    handler_id:str=GLOBAL_ALTERNATIVE_HANDLER
    schema_version:int=1
    family:str='casting.alternative-cost.fixed-battlefield-grant'
    event:str=GLOBAL_ALTERNATIVE_EVENT
    rule_references:tuple[str,...]=('118.9','118.9a','118.9b','118.9c','118.9d','107.3b','601.2f','601.2h','400.7')
    capability_dependencies:tuple[str,...]=(GLOBAL_ALTERNATIVE_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','requires_complete_card_program','grant'},field='global alternative cost')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
            or descriptor['schema_version']!=1 or descriptor['event']!=self.event
            or descriptor['requires_complete_card_program'] is not True):
            raise SemanticNodeError('Global alternative requires its exact identity and complete source')
        try:return GlobalAlternativeCostSpec.from_dict(descriptor['grant'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        del context
        return (self.validate(descriptor),)
