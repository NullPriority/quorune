from __future__ import annotations

"""Closed fixed Waterbend cost and identity-pinned nonmana payment facts."""

from collections.abc import Mapping
from dataclasses import dataclass

WATERBEND_CAPABILITY='cost.waterbend.fixed'
WATERBEND_KIND='waterbend'
WATERBEND_EVENT='player.waterbent'
WATERBEND_COMPOSITION_CAPABILITY='casting.additional_cost.waterbend_result_composition'
WATERBEND_COMPOSITION_MECHANIC='fixed-waterbend-price-result-composition'


@dataclass(frozen=True,slots=True)
class FixedWaterbendCost:
    generic: int

    def __post_init__(self):
        if type(self.generic) is not int or self.generic<0:raise ValueError('Waterbend requires a fixed nonnegative generic cost')

    def to_dict(self):return {'schema_version':1,'kind':WATERBEND_KIND,'generic':self.generic}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','kind','generic'}
                or type(value['schema_version']) is not int or value['schema_version']!=1 or value['kind']!=WATERBEND_KIND):
            raise ValueError('Fixed Waterbend cost fields are closed')
        return cls(value['generic'])


@dataclass(frozen=True,slots=True)
class WaterbendObject:
    object_id: str
    ref: str
    logical_object_id: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.object_id,self.ref,self.logical_object_id)):
            raise ValueError('Waterbend object requires physical, public and logical identity')

    def to_dict(self):return {'object_id':self.object_id,'ref':self.ref,'logical_object_id':self.logical_object_id}


@dataclass(frozen=True,slots=True)
class WaterbendPayment:
    cost: FixedWaterbendCost
    objects: tuple[WaterbendObject,...]

    def __post_init__(self):
        if (not isinstance(self.cost,FixedWaterbendCost) or type(self.objects) is not tuple
                or any(not isinstance(value,WaterbendObject) for value in self.objects) or len(self.objects)>self.cost.generic
                or any(len({getattr(value,key) for value in self.objects})!=len(self.objects) for key in ('object_id','ref','logical_object_id'))):
            raise ValueError('Waterbend payment requires distinct objects within its own generic cost')

    def to_dict(self):return {'schema_version':1,'cost':self.cost.to_dict(),'objects':[value.to_dict() for value in self.objects]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','cost','objects'}
                or type(value['schema_version']) is not int or value['schema_version']!=1 or type(value['objects']) is not list):
            raise ValueError('Waterbend payment fields are closed')
        objects=[]
        for row in value['objects']:
            if not isinstance(row,Mapping) or set(row)!={'object_id','ref','logical_object_id'}:raise ValueError('Waterbend object fields are closed')
            objects.append(WaterbendObject(**row))
        return cls(FixedWaterbendCost.from_dict(value['cost']),tuple(objects))


def fixed_waterbend_cost(value):
    return FixedWaterbendCost.from_dict(value) if isinstance(value,Mapping) and value.get('kind')==WATERBEND_KIND else None


def waterbend_node_capabilities(*,cost_schema):
    if not isinstance(cost_schema,Mapping):return ()
    raw=cost_schema.get('waterbend')
    if raw is None:
        costs=cost_schema.get('additional_costs',())
        if not isinstance(costs,(list,tuple)) or len(costs)!=1 or not isinstance(costs[0],Mapping) or costs[0].get('kind')!='waterbend':return ()
        raw=costs[0]
    try:FixedWaterbendCost.from_dict(raw)
    except (TypeError,ValueError):return ()
    return (WATERBEND_CAPABILITY,)
