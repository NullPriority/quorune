from __future__ import annotations

"""Typed all-zone characteristic fragments in the ability-component registry."""

from dataclasses import dataclass
from ..all_colors import AllColorsCharacteristicDefinitionSpec,ALL_COLORS_HANDLER,ALL_COLORS_CAPABILITY
from ..basic_land_types import (
    ALL_NONBASIC_LAND_TYPES_CAPABILITY, ALL_NONBASIC_LAND_TYPES_HANDLER, AllNonbasicLandTypesDefinitionSpec,
)


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




@dataclass(frozen=True, slots=True)
class AllNonbasicLandTypesDefinitionHandler:
    handler_id: str = ALL_NONBASIC_LAND_TYPES_HANDLER
    schema_version: int = 1
    family: str = ALL_NONBASIC_LAND_TYPES_CAPABILITY
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("205.3i", "305.6", "604.3", "604.3a", "613.1d", "613.3")
    capability_dependencies: tuple[str, ...] = (ALL_NONBASIC_LAND_TYPES_CAPABILITY,)

    def validate(self, descriptor):
        from .ability_fragments import _fragment

        return _fragment(descriptor, handler_id=self.handler_id, event=self.event,
                         expected_type=AllNonbasicLandTypesDefinitionSpec)

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
