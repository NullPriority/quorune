from __future__ import annotations

"""Immutable fixed alternative mana costs granted by a battlefield source."""

from dataclasses import dataclass
from typing import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value

GLOBAL_ALTERNATIVE_CAPABILITY='casting.alternative_cost.fixed_battlefield_grant'
GLOBAL_ALTERNATIVE_HANDLER='casting.alternative-cost.fixed-battlefield-grant.v1'
GLOBAL_ALTERNATIVE_EVENT='cast.cost'
MANA_KEYS=frozenset({'W','U','B','R','G','C','GENERIC'})


@dataclass(frozen=True,slots=True)
class GlobalAlternativeCostSpec:
    mana: FrozenMap
    predicate: ObjectQuerySpec=ObjectQuerySpec()
    origin: str='any'
    without_mana_cost: bool=False

    def __post_init__(self):
        if not isinstance(self.mana,FrozenMap):
            if not isinstance(self.mana,Mapping):raise ValueError('Global alternative mana must be an object')
            object.__setattr__(self,'mana',FrozenMap(self.mana))
        if set(self.mana)!=MANA_KEYS or any(type(v) is not int or v<0 for v in self.mana.values()):
            raise ValueError('Global alternative mana requires canonical nonnegative amounts')
        if type(self.without_mana_cost) is not bool or self.origin not in {'any','hand'}:
            raise ValueError('Global alternative origin or payment mode is unsupported')
        if self.without_mana_cost and any(self.mana.values()):raise ValueError('Without-mana alternative cannot charge a base mana cost')
        if not isinstance(self.predicate,ObjectQuerySpec) or self.predicate!=ObjectQuerySpec(
            types_all=self.predicate.types_all,subtypes_all=self.predicate.subtypes_all):
            raise ValueError('Global alternative selector must use only fixed card types and subtypes')

    def to_dict(self):
        return {'schema_version':1,'mana':thaw_value(self.mana),'predicate':self.predicate.to_dict(),
            'origin':self.origin,'without_mana_cost':self.without_mana_cost}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','mana','predicate','origin','without_mana_cost'}:
            raise ValueError('Global alternative cost fields are closed')
        if type(value['schema_version']) is not int or value['schema_version']!=1:
            raise ValueError('Unsupported global alternative cost version')
        return cls(FrozenMap(value['mana']),ObjectQuerySpec.from_dict(value['predicate']),
            value['origin'],value['without_mana_cost'])
