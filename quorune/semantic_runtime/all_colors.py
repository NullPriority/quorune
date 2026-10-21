from __future__ import annotations

"""Typed all-colors fragment in the existing ability-component registry."""

from dataclasses import dataclass
from ..all_colors import AllColorsCharacteristicDefinitionSpec,ALL_COLORS_HANDLER,ALL_COLORS_CAPABILITY


@dataclass(frozen=True,slots=True)
class AllColorsDefinitionHandler:
    handler_id:str=ALL_COLORS_HANDLER
    schema_version:int=1
    family:str='ability.static.all_colors_characteristic_definition'
    event:str='continuous'
    rule_references:tuple[str,...]=('105.1','105.2','604.3','604.3a','613.1e','613.3','613.6')
    capability_dependencies:tuple[str,...]=(ALL_COLORS_CAPABILITY,)

    def validate(self,descriptor):
        from .ability_fragments import _fragment
        return _fragment(descriptor,handler_id=self.handler_id,event=self.event,expected_type=AllColorsCharacteristicDefinitionSpec)

    def lower(self,descriptor,context):return (self.validate(descriptor),)
