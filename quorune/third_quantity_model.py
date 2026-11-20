"""Versioned third arithmetic; existing half-quantity encodings stay separate."""
from collections.abc import Mapping
from dataclasses import dataclass

THIRD_QUANTITY_KIND='third_quantity'
THIRD_LIFE_CAPABILITY='life.loss.third_current_player'
THIRD_LIFE_MECHANIC='third-current-player-life-loss'


@dataclass(frozen=True,slots=True)
class ThirdQuantitySpec:
    rounding: str
    divisor: int=3
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.divisor) is not int or self.divisor!=3
                or type(self.rounding) is not str or self.rounding not in {'up','down'}):
            raise ValueError('Third quantity requires version one, divisor three and explicit rounding')

    def amount(self,value):
        if type(value) is not int:raise ValueError('Third quantity requires an integer domain')
        return (max(0,value)+(2 if self.rounding=='up' else 0))//3

    def to_dict(self):
        return {'kind':THIRD_QUANTITY_KIND,'schema_version':1,'divisor':3,'rounding':self.rounding}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','divisor','rounding'}
                or value['kind']!=THIRD_QUANTITY_KIND):
            raise ValueError('Third quantity fields are closed')
        return cls(value['rounding'],value['divisor'],value['schema_version'])
