from __future__ import annotations

"""Closed additive and unlimited CR 509 creature blocking permissions."""

from dataclasses import dataclass
from typing import Mapping

BLOCK_CAPACITY_CAPABILITY = 'combat.block.capacity'
BLOCK_CAPACITY_HANDLER = 'ability.static.block-capacity.v1'


@dataclass(frozen=True, slots=True)
class BlockCapacitySpec:
    additional: int = 0
    unlimited: bool = False
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported blocking-capacity schema')
        if type(self.additional) is not int or not 0 <= self.additional <= 99:
            raise ValueError('Additional blocking capacity must be a fixed integer from zero to 99')
        if type(self.unlimited) is not bool or (self.unlimited and self.additional) or (not self.unlimited and not self.additional):
            raise ValueError('Blocking capacity requires additional or unlimited permission')

    def to_dict(self):
        return {'schema_version':self.schema_version,'additional':self.additional,'unlimited':self.unlimited}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','additional','unlimited'}:
            raise ValueError('Blocking capacity has a closed schema')
        return cls(**value)
