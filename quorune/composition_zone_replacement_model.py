"""Immutable card-component scope for CR 730.3e in one replacement event."""
from collections.abc import Mapping
from dataclasses import dataclass


COMPONENTS_FIELD='composition_card_components'
RESULT_FIELD='composition_card_event'


@dataclass(frozen=True,slots=True)
class CardZoneComponent:
    object_id: str
    logical_object_id: str
    object_ref: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in
                (self.object_id,self.logical_object_id,self.object_ref)):
            raise ValueError('Card-component zone identities must be nonempty strings')

    def to_dict(self):
        return {'object_id':self.object_id,'logical_object_id':self.logical_object_id,'object_ref':self.object_ref}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'object_id','logical_object_id','object_ref'}:
            raise ValueError('Card-component zone identities have a closed schema')
        return cls(**value)


def card_zone_components(values):
    if not isinstance(values,(tuple,list)) or not values or any(not isinstance(row,CardZoneComponent) for row in values):
        raise ValueError('Card-component zone scope requires captured physical card identities')
    result=tuple(values)
    for field in ('object_id','logical_object_id','object_ref'):
        if len({getattr(row,field) for row in result})!=len(result):
            raise ValueError('Card-component zone identities must be unique')
    return result
