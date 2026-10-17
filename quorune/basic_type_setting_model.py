from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from .basic_land_types import BASIC_LAND_MANA

BASIC_TYPE_SETTING_HANDLER='continuous.basic_land_type.fixed-setting.v1'
BASIC_TYPE_SETTING_CAPABILITY='continuous.basic_land_type.fixed_setting'


@dataclass(frozen=True,slots=True)
class BasicTypeSettingSpec:
    scope:str
    basic_types:tuple[str,...]

    def __post_init__(self):
        if type(self.scope) is not str or self.scope not in {'nonbasic_lands','controller_lands','attached_land'}:
            raise ValueError('Basic type setting requires a fixed land scope')
        if (not isinstance(self.basic_types,tuple) or not self.basic_types
            or any(type(t) is not str or t not in BASIC_LAND_MANA for t in self.basic_types)
            or self.basic_types!=tuple(sorted(set(self.basic_types)))):
            raise ValueError('Basic type setting requires canonical distinct basic types')

    def to_dict(self):return {'scope':self.scope,'basic_types':list(self.basic_types)}

    @classmethod
    def from_dict(cls,value:Mapping):
        if not isinstance(value,Mapping) or set(value)!={'scope','basic_types'} or not isinstance(value['basic_types'],list):
            raise ValueError('Basic type setting has a closed schema')
        return cls(value['scope'],tuple(value['basic_types']))
