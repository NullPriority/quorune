from __future__ import annotations

"""Closed, recipient-local quantities for live layer-7c modifiers."""

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping
from .counter_names import normalized_counter_name

RECIPIENT_MODIFIER_CAPABILITY='continuous.power_toughness.recipient_quantity'
RECIPIENT_MODIFIER_OPERATION='modify_power_toughness_by_recipient_quantity'


class RecipientQuantityKind(str,Enum):
    COLORS='colors'
    COUNTERS='counters'
    MANA_SYMBOLS='mana_symbols'
    CREATURE_TYPES='creature_types'


@dataclass(frozen=True,slots=True)
class RecipientQuantitySpec:
    kind: RecipientQuantityKind
    counter: str|None=None
    mana_color: str|None=None
    maximum: int|None=None
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1 or not isinstance(self.kind,RecipientQuantityKind):
            raise ValueError('Recipient quantity identity is malformed')
        if self.kind is RecipientQuantityKind.COUNTERS:
            if type(self.counter) is not str or normalized_counter_name(self.counter)!=self.counter:
                raise ValueError('Recipient counter must be a canonical fixed counter name')
        elif self.counter is not None:raise ValueError('Only counter quantities name a counter')
        if self.kind is RecipientQuantityKind.MANA_SYMBOLS:
            if type(self.mana_color) is not str or self.mana_color not in {'W','U','B','R','G'}:
                raise ValueError('Recipient mana quantities require one fixed color')
        elif self.mana_color is not None:raise ValueError('Only mana quantities name a color')
        if self.maximum is not None and (type(self.maximum) is not int or self.maximum<1):
            raise ValueError('Recipient quantity maximum must be a positive integer')

    def to_dict(self):
        return {'schema_version':1,'kind':self.kind.value,'counter':self.counter,
            'mana_color':self.mana_color,'maximum':self.maximum}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','kind','counter','mana_color','maximum'}:
            raise ValueError('Recipient quantity fields are malformed')
        return cls(kind=RecipientQuantityKind(value['kind']),counter=value['counter'],
            mana_color=value['mana_color'],maximum=value['maximum'],schema_version=value['schema_version'])


def validate_recipient_modifier(value):
    if not isinstance(value,Mapping) or set(value)!={'schema_version','quantity','power','toughness'}:
        raise ValueError('Recipient modifier fields are malformed')
    if type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('Recipient modifier version is malformed')
    if any(type(value[key]) is not int for key in ('power','toughness')) or not(value['power'] or value['toughness']):
        raise ValueError('Recipient modifier requires nonzero integer coefficients')
    return RecipientQuantitySpec.from_dict(value['quantity'])
