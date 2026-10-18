from __future__ import annotations

"""Fixed targeting prohibitions in the existing static component registry."""

from dataclasses import dataclass
from ..target_restrictions import TargetRestrictionSpec,TARGET_RESTRICTION_HANDLER,TARGET_RESTRICTION_CAPABILITY
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class TargetRestrictionHandler:
    handler_id: str=TARGET_RESTRICTION_HANDLER
    schema_version: int=1
    family: str='ability.static.target_prohibition'
    event: str='characteristics.evaluate'
    rule_references: tuple[str,...]=('115.1a','115.1b','115.1c','115.1d','608.2b','611.3a','611.3b')
    capability_dependencies: tuple[str,...]=(TARGET_RESTRICTION_CAPABILITY,)

    def validate(self,value):
        exact_fields(value,{'handler_id','schema_version','event','restrictions'},field='Target prohibition')
        if (value['handler_id']!=self.handler_id or type(value['schema_version']) is not int or value['schema_version']!=1
                or value['event']!=self.event or type(value['restrictions']) not in (list,tuple) or not value['restrictions']):
            raise SemanticNodeError('Target prohibition requires closed static descriptors')
        try:return tuple(TargetRestrictionSpec.from_dict(v) for v in value['restrictions'])
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,value,context):self.validate(value);return ()
