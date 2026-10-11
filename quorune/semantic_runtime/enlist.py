from __future__ import annotations

from dataclasses import dataclass
from ..enlist_model import EnlistSpec,ENLIST_CAPABILITY,ENLIST_HANDLER
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class EnlistAbilityFragmentHandler:
    handler_id: str=ENLIST_HANDLER
    schema_version: int=1
    family: str='ability.static.enlist'
    event: str='characteristics.evaluate'
    rule_references: tuple[str,...]=('702.154a','702.154b','702.154c','702.154d','508.1g','603.12')
    capability_dependencies: tuple[str,...]=(ENLIST_CAPABILITY,)

    def validate(self,value):
        exact_fields(value,{'handler_id','schema_version','event','fragment'},field='Enlist handler')
        if value['handler_id']!=self.handler_id or type(value['schema_version']) is not int or value['schema_version']!=1 or value['event']!=self.event:raise SemanticNodeError('Enlist identity is malformed')
        from ..ability_fragments import ability_fragment_from_dict
        spec=ability_fragment_from_dict(value['fragment'])
        if not isinstance(spec,EnlistSpec):raise SemanticNodeError('Enlist requires its typed instance')
        return spec

    def lower(self,value,context):return (self.validate(value),)
