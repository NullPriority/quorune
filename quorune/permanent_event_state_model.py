"""Versioned public permanent state, separate from derived characteristics."""
from dataclasses import dataclass,fields
from collections.abc import Mapping

PERMANENT_EVENT_STATE_KEY='public_permanent_event_state'
PERMANENT_EVENT_STATE_PREFIX='permanent_state_'
ZONE_EVENT_NUMERICS_KEY = 'public_zone_event_numerics'
ZONE_EVENT_NUMERICS_VERSION = 2
STATE_QUALIFIED_ZONE_CAPABILITY='trigger.event.state_qualified_zone_change'
STATE_QUALIFIED_DAMAGE_CAPABILITY='trigger.event.state_qualified_damage'


def permanent_event_state_version(value):
    if value is not None and (type(value) is not int or value not in {1, ZONE_EVENT_NUMERICS_VERSION}):
        raise ValueError('Unsupported permanent event-state execution version')
    return value


@dataclass(frozen=True,slots=True)
class PermanentEventState:
    tapped: bool
    attacking: bool
    blocking: bool
    enchanted: bool
    equipped: bool
    modified: bool
    monstrous: bool
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:
            raise ValueError('Unsupported permanent event-state facts version')
        if any(type(getattr(self,f.name)) is not bool for f in fields(self) if f.name!='schema_version'):
            raise ValueError('Permanent event-state facts require exact booleans')

    def to_dict(self):return {f.name:getattr(self,f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={f.name for f in fields(cls)}:
            raise ValueError('Permanent event-state facts have a closed schema')
        return cls(**dict(value))

    def context(self):
        return {PERMANENT_EVENT_STATE_PREFIX+('version' if key=='schema_version' else key):value
            for key,value in self.to_dict().items()}


def sealed_permanent_state_context(characteristics):
    if PERMANENT_EVENT_STATE_KEY not in characteristics:return {}
    return PermanentEventState.from_dict(characteristics[PERMANENT_EVENT_STATE_KEY]).context()


def damage_permanent_state_context(facts):
    return {} if facts is None else {'source_'+key:value for key,value in facts.context().items()}


def permanent_state_from_damage_context(payload):
    prefix='source_'+PERMANENT_EVENT_STATE_PREFIX
    values={key.removeprefix(prefix):value for key,value in payload.items() if key.startswith(prefix)}
    if not values:return None
    return PermanentEventState.from_dict({('schema_version' if key=='version' else key):value
        for key,value in values.items()})


def _state_event_dependencies(program,*,prefix,events,capability):
    found=False;version=False
    names={f.name for f in fields(PermanentEventState)}-{'schema_version'}
    def visit(value):
        nonlocal found,version
        if isinstance(value,Mapping):
            field=value.get('field')
            if isinstance(field,str) and field.startswith(prefix):
                found=True
                if set(value)!={'field','op','value'} or value['op']!='eq':
                    raise ValueError('Permanent state conditions have closed equality fields')
                name=field.removeprefix(prefix)
                if name=='version':
                    if type(value['value']) is not int or value['value']!=1:raise ValueError('Permanent state condition version must be1')
                    version=True
                elif name not in names or type(value['value']) is not bool:
                    raise ValueError('Permanent state conditions require a represented boolean')
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(program.event_condition)
    if found and (not version or program.event.removesuffix('.self') not in events):
        raise ValueError('Permanent state conditions require a versioned supported occurrence')
    return (capability,) if found else ()


def state_qualified_zone_runtime_dependencies(program):
    return _state_event_dependencies(program,prefix=PERMANENT_EVENT_STATE_PREFIX,
        events={'permanent.enter','creature.dies','permanent.leave','permanent.graveyard'},
        capability=STATE_QUALIFIED_ZONE_CAPABILITY)


def state_qualified_damage_runtime_dependencies(program):
    return _state_event_dependencies(program,prefix='source_'+PERMANENT_EVENT_STATE_PREFIX,
        events={'damage.dealt'},capability=STATE_QUALIFIED_DAMAGE_CAPABILITY)
