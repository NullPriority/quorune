"""Pinned entry X, distinct from the entered permanent's counters or value."""
from dataclasses import dataclass
from collections.abc import Mapping

RAVENOUS_CAPABILITY='keyword.ravenous.entry_x_and_draw'
ENTRY_X_FIELD='source_entry_x_information'
ENTRY_X_CONDITION='ravenous_entry_x_at_least'


@dataclass(frozen=True,slots=True)
class EntryXInformation:
    object_id: str
    logical_object_id: str
    amount: int
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.object_id) is not str or not self.object_id
                or type(self.logical_object_id) is not str or not self.logical_object_id.startswith(self.object_id+'@')
                or not self.logical_object_id.removeprefix(self.object_id+'@').isdigit()
                or type(self.amount) is not int or self.amount<0):raise ValueError('Entry X requires a versioned permanent incarnation and nonnegative integer')

    def to_dict(self):return {'schema_version':1,'object_id':self.object_id,'logical_object_id':self.logical_object_id,'amount':self.amount}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','object_id','logical_object_id','amount'}:raise ValueError('Entry X information has a closed schema')
        return cls(**value)


def ravenous_condition():return {'field':ENTRY_X_CONDITION,'op':'eq','value':True}


def validate_ravenous_condition(value):
    if value!=ravenous_condition() or type(value.get('value')) is not bool:raise ValueError('Ravenous has one fixed threshold condition')


def ravenous_runtime_dependencies(program):
    condition=program.event_condition
    mentions=isinstance(condition,Mapping) and condition.get('field')==ENTRY_X_CONDITION
    marker='ravenous_entry_x' in program.coverage
    if not mentions and not marker:return ()
    if not mentions:raise ValueError('Ravenous coverage requires its closed threshold condition')
    validate_ravenous_condition(condition)
    if (program.event!='permanent.enter.self' or program.active_zone!='battlefield' or not marker
            or 'intervening_condition' not in program.coverage or program.target_schema is not None
            or tuple(program.effects)!=({'op':'draw','player':'$controller','count':1,'private':True},)):
        raise ValueError('Ravenous threshold requires its original self-entry envelope')
    return (RAVENOUS_CAPABILITY,)
