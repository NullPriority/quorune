"""Public creature-type designation reads at represented event discovery."""
from collections.abc import Mapping
from ..entry_designations import EntryDesignationKind,validate_designation

CHOSEN_TYPE_EVENT_CAPABILITY='trigger.event.chosen_creature_type'
CHOSEN_TYPE_FIELD='source_annotation.chosen_creature_type'


def chosen_type_event_value(source):
    value=source.annotations.get('chosen_creature_type')
    return None if value is None else validate_designation(EntryDesignationKind.CREATURE_TYPE,value)


def chosen_type_event_dependencies(program):
    def uses(value):
        if isinstance(value,Mapping):
            return value.get('field')==CHOSEN_TYPE_FIELD or any(uses(v) for v in value.values())
        return isinstance(value,(tuple,list)) and any(uses(v) for v in value)
    return (CHOSEN_TYPE_EVENT_CAPABILITY,) if uses(program.event_condition) else ()
