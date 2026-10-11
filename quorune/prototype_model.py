from __future__ import annotations

"""Closed CR 718 alternate characteristics of a prototyped spell or permanent."""

from dataclasses import dataclass
import re
from typing import Mapping
from .util import mana_cost_to_vector

PROTOTYPE_CAPABILITY='casting.prototype.characteristics'
PROTOTYPE_HANDLER='ability.static.prototype.v1'
PROTOTYPE_ANNOTATION='prototype_characteristics'


@dataclass(frozen=True,slots=True)
class PrototypeSpec:
    mana_cost: str
    power: int
    toughness: int
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:
            raise ValueError('Unsupported Prototype characteristic schema')
        if type(self.mana_cost) is not str or re.fullmatch(r'(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+',self.mana_cost) is None:
            raise ValueError('Prototype requires a fixed ordinary printed mana cost')
        if any(type(v) is not int or v<0 for v in (self.power,self.toughness)):
            raise ValueError('Prototype requires nonnegative exact power and toughness')

    def to_dict(self):
        return {'schema_version':self.schema_version,'mana_cost':self.mana_cost,'power':self.power,'toughness':self.toughness}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','mana_cost','power','toughness'}:
            raise ValueError('Prototype characteristics have a closed schema')
        return cls(**value)

    def characteristic_values(self):
        cost,_complex=mana_cost_to_vector(self.mana_cost)
        return {'mana_cost':self.mana_cost,'mana_value':sum(cost.values()),
                'colors':[c for c in 'WUBRG' if cost.get(c,0)],
                'power':str(self.power),'toughness':str(self.toughness)}
