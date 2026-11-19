from __future__ import annotations

"""Two printed colored unit costs per age, followed by one whole payment."""
from dataclasses import dataclass
from collections.abc import Mapping
import re

CUMULATIVE_ALTERNATIVE_MANA_CAPABILITY='counter.producer.cumulative_upkeep_alternative_mana'
_COLORS=frozenset('WUBRG')
_KEYS=('GENERIC','W','U','B','R','G','C')

@dataclass(frozen=True,slots=True)
class CumulativeAlternativeManaSpec:
    colors: tuple[str,str]
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version)is not int or self.schema_version!=1:
            raise ValueError('Alternative upkeep price requires descriptor one')
        if not isinstance(self.colors,tuple) or len(self.colors)!=2 or any(type(c)is not str or c not in _COLORS for c in self.colors) or self.colors[0]==self.colors[1]:
            raise ValueError('Alternative upkeep requires two distinct printed colors')

    def to_dict(self):
        return {'schema_version':self.schema_version,'colors':list(self.colors)}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','colors'} or not isinstance(value['colors'],(tuple,list)):
            raise ValueError('Alternative upkeep descriptor fields are malformed')
        return cls(tuple(value['colors']),value['schema_version'])

    def effect(self):
        return {'op':'offer_optional_mana_payment','schema_version':9,'player':'$controller','source':'$source.zone_object','alternative_mana_price':self.to_dict()}

    @classmethod
    def from_effect(cls,value,*,actor='$controller',source='$source.zone_object'):
        if not isinstance(value,Mapping) or set(value)!={'op','schema_version','player','source','alternative_mana_price'} or value['op']!='offer_optional_mana_payment' or type(value['schema_version'])is not int or value['schema_version']!=9 or value['player']!=actor or value['source']!=source:
            raise ValueError('Alternative upkeep requires the bound version-nine wrapper')
        return cls.from_dict(value['alternative_mana_price'])

    def declared_count(self,age,counts):
        if type(age)is not int or age<0 or not isinstance(counts,Mapping) or set(counts)-set(self.colors) or any(type(n)is not int or n<0 for n in counts.values()):
            raise ValueError('Alternative upkeep proposal counts are malformed')
        total=sum(counts.values())
        if total>age:raise ValueError('Too many proposed age costs')
        return total

    def propose(self,age,counts,color):
        total=self.declared_count(age,counts)
        if type(color)is not str or color not in self.colors or total>=age:
            raise ValueError('Choose one printed color for an unfilled age cost')
        return {c:counts.get(c,0)+int(c==color) for c in self.colors}

    def requirements(self,age,counts):
        if self.declared_count(age,counts)!=age:raise ValueError('Pay the entire declared upkeep vector or none')
        return {key:counts.get(key,0) for key in _KEYS}

    @property
    def capabilities(self):
        return (CUMULATIVE_ALTERNATIVE_MANA_CAPABILITY,'counter.producer.fixed_player_effect','effect.choice.optional_fixed_mana_payment','trigger.source.fixed_maintenance','zone.change.destination_replacement')


def compile_alternative_mana_upkeep(text):
    match=re.fullmatch(r'Cumulative upkeep \{(?P<first>[WUBRG])\} or \{(?P<second>[WUBRG])\}\.?',text,re.I)
    if match is None:return None
    first,second=match['first'].upper(),match['second'].upper()
    return CumulativeAlternativeManaSpec((first,second)) if first!=second else None
