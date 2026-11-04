"""Closed physical component data for commander destination replacement."""
from collections.abc import Mapping
from dataclasses import dataclass

COMPONENTS_FIELD='commander_components'
RETURNS_FIELD='commander_component_returns'
EPOCH_FIELD='commander_destination_epoch'


@dataclass(frozen=True,slots=True)
class CommanderZoneComponent:
    object_id: str
    logical_object_id: str
    ref: str
    designation_id: str
    owner: str
    printed_name: str

    def __post_init__(self):
        if any(type(value) is not str or not value or value.strip()!=value for value in (
                self.object_id,self.logical_object_id,self.ref,self.designation_id,self.owner,self.printed_name)):
            raise ValueError('Commander component requires exact physical identity and owner')
        if not self.logical_object_id.startswith(self.object_id+'@'):
            raise ValueError('Commander component incarnation does not match its physical object')

    def to_dict(self):
        return {field:getattr(self,field) for field in (
            'object_id','logical_object_id','ref','designation_id','owner','printed_name')}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={
                'object_id','logical_object_id','ref','designation_id','owner','printed_name'}:
            raise ValueError('Commander component fields are closed')
        return cls(**value)


def commander_zone_components(value):
    if not isinstance(value,(list,tuple)) or any(not isinstance(row,CommanderZoneComponent) for row in value):
        raise ValueError('Commander components must be a typed identity tuple')
    values=tuple(value)
    for field in ('object_id','designation_id','ref'):
        if len({getattr(row,field) for row in values})!=len(values):
            raise ValueError('Commander component identities must be unique')
    return values


def commander_subject_components(value,*,is_commander,owner):
    components=commander_zone_components(value)
    if components and (is_commander is not True or any(row.owner!=owner for row in components)):
        raise ValueError('Commander components must match the affected permanent owner')
    return components


def payload_commander_components(payload):
    if COMPONENTS_FIELD not in payload:
        if RETURNS_FIELD in payload or EPOCH_FIELD in payload:raise ValueError('Commander returns require their physical component snapshot')
        return ()
    rows=payload[COMPONENTS_FIELD]
    epoch=payload.get(EPOCH_FIELD,0)
    if type(epoch) is not int or epoch<0:raise ValueError('Commander destination epoch must be a nonnegative integer')
    if not isinstance(rows,(list,tuple)):raise ValueError('Commander component snapshot must be an array')
    return commander_zone_components(tuple(CommanderZoneComponent.from_dict(row) for row in rows))


def validate_commander_component_returns(payload,values):
    components=payload_commander_components(payload)
    if (not components or not isinstance(values,(list,tuple)) or any(type(value) is not str for value in values)
            or len(values)!=len(set(values))
            or set(values)-{component.designation_id for component in components}):
        raise ValueError('Commander returns must name known physical designations exactly once')
    return tuple(values)


def commander_component_destinations(payload):
    components=payload_commander_components(payload)
    if not components:return {}
    selected=validate_commander_component_returns(payload,payload.get(RETURNS_FIELD,()))
    return {component.object_id:'command' for component in components if component.designation_id in selected}
