from __future__ import annotations

"""Immutable fixed mana-value limit over already evaluated characteristics."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True,slots=True)
class ManaValueLimitSpec:
    maximum: int
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.maximum) is not int or self.maximum<0):
            raise ValueError('Mana-value limit requires a nonnegative fixed maximum and supported schema')

    def permits(self,mana_value):
        if (type(mana_value) not in (int,float) or type(mana_value) is float and not isfinite(mana_value)
                or mana_value<0):
            raise ValueError('Mana-value comparison requires a canonical nonnegative numeric value')
        return mana_value<=self.maximum

    def to_dict(self):
        return {'schema_version':1,'maximum':self.maximum}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','maximum'}:
            raise ValueError('Mana-value limit has a closed schema')
        return cls(**dict(value))
