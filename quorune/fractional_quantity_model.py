from __future__ import annotations

"""Closed half-quantity arithmetic with explicit Magic rounding."""

from dataclasses import dataclass
from typing import Mapping


HALF_QUANTITY_KIND = 'half_quantity'
HALF_LIFE_OPERATION = 'lose_life'
HALF_LIFE_MECHANIC = 'half-current-player-life-loss'
HALF_LIFE_CAPABILITY = 'life.loss.half_current_player'
HALF_CHOICE_MECHANIC = 'half-affected-player-domain-choice'
HALF_CHOICE_CAPABILITY = 'choice.affected_player.half_domain'


@dataclass(frozen=True, slots=True)
class HalfQuantitySpec:
    rounding: str
    divisor: int = 2
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported half-quantity version')
        if type(self.divisor) is not int or self.divisor != 2 or type(self.rounding) is not str or self.rounding not in {'up', 'down'}:
            raise ValueError('Half quantities require divisor two and explicit rounding')

    def amount(self, value: int) -> int:
        if type(value) is not int:
            raise ValueError('Half quantity requires an integer domain')
        value = max(0, value)
        return (value + (1 if self.rounding == 'up' else 0)) // 2

    def to_dict(self):
        return {'kind': HALF_QUANTITY_KIND, 'schema_version': self.schema_version,
                'divisor': self.divisor, 'rounding': self.rounding}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'divisor', 'rounding'}
                or value['kind'] != HALF_QUANTITY_KIND):
            raise ValueError('Half quantity fields are missing or unknown')
        return cls(value['rounding'], value['divisor'], value['schema_version'])
