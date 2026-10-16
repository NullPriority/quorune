from __future__ import annotations

"""Typed static marker and spell instruction for CR 702.131 Ascend."""

from dataclasses import dataclass
from ..city_blessing_model import ASCEND_STATIC_HANDLER,CITYS_BLESSING_CAPABILITY,AscendIntent
from .component_registry import exact_fields
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True,slots=True)
class AscendStaticHandler:
    handler_id: str=ASCEND_STATIC_HANDLER
    schema_version: int=1
    family: str='ability.static.ascend'
    event: str='characteristics.evaluate'
    rule_references: tuple[str,...]=('702.131b','702.131c','702.131d')
    capability_dependencies: tuple[str,...]=(CITYS_BLESSING_CAPABILITY,)

    def validate(self,value):
        exact_fields(value,{'handler_id','schema_version','event'},field='Ascend static marker')
        if value['handler_id']!=self.handler_id or type(value['schema_version']) is not int or value['schema_version']!=1 or value['event']!=self.event:
            raise SemanticNodeError('Ascend static marker is malformed')
        return True

    def lower(self,value,context):
        self.validate(value);return ()


@dataclass(frozen=True,slots=True)
class AscendSpellHandler:
    operation: str='ascend'
    handler_id: str='generic.ascend.v1'
    schema_version: int=1
    family: str='player.designation.citys_blessing'
    rule_references: tuple[str,...]=('702.131a','702.131c','702.131d')
    capability_dependencies: tuple[str,...]=(CITYS_BLESSING_CAPABILITY,)

    def lower(self,effect,context):
        exact_fields(effect,{'op','player'},field='Ascend spell instruction')
        if effect['op']!=self.operation or effect['player']!=context.actor:
            raise SemanticNodeError('Ascend spell instruction requires its controller')
        context.query.require_active_seat(context.actor)
        return IntentPlan(operation=self.operation,handler_id=self.handler_id,
            intents=(AscendIntent(context.actor,context.default_reason),))
