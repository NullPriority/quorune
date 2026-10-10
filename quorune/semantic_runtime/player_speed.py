from __future__ import annotations

"""Validate the current Start Your Engines battlefield static marker."""

from dataclasses import dataclass
from .component_registry import exact_fields
from .context import SemanticNodeError
from ..rules.player_speed_model import START_ENGINES_HANDLER,SPEED_CAPABILITY


@dataclass(frozen=True,slots=True)
class StartEnginesHandler:
    handler_id: str=START_ENGINES_HANDLER
    schema_version: int=1
    family: str='ability.static.start_your_engines'
    event: str='characteristics.evaluate'
    rule_references: tuple[str,...]=('702.179a','702.179d','704.5aa')
    capability_dependencies: tuple[str,...]=(SPEED_CAPABILITY,)

    def validate(self,value):
        exact_fields(value,{'handler_id','schema_version','event'},field='Start Your Engines marker')
        if value['handler_id']!=self.handler_id or type(value['schema_version']) is not int or value['schema_version']!=1 or value['event']!=self.event:raise SemanticNodeError('Start Your Engines marker is malformed')
        return True

    def lower(self,value,context):
        self.validate(value)
        return ()
