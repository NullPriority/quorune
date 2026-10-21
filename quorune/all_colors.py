from __future__ import annotations

"""One fixed all-zone color definition and its layer-five effect."""

from collections.abc import Mapping
from dataclasses import dataclass

ALL_COLORS=('W','U','B','R','G')
ALL_COLORS_CAPABILITY='continuous.characteristics.all_colors_definition'
ALL_COLORS_HANDLER='ability.static.all-colors-characteristic-definition.v1'


@dataclass(frozen=True,slots=True)
class AllColorsCharacteristicDefinitionSpec:
    schema_version:int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:
            raise ValueError('All-colors definitions use schema one')

    def to_dict(self):return {'schema_version':1}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version'}:
            raise ValueError('All-colors definitions have a closed schema')
        return cls(value['schema_version'])
