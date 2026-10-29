"""Immutable public facts for one committed permanent designation transition."""
from dataclasses import dataclass

DESIGNATION_EVENT_CAPABILITY = 'trigger.event.normalized_permanent_designation'
DESIGNATION_EVENT_MECHANIC = 'trigger-event-normalized-permanent-designation'
MONSTROUS_EVENT = 'permanent.designated_monstrous'
RENOWNED_EVENT = 'permanent.designated_renowned'
DESIGNATION_EVENTS = {MONSTROUS_EVENT: 'permanent.designation.monstrous',
                      RENOWNED_EVENT: 'permanent.designation.renowned'}


@dataclass(frozen=True, slots=True)
class PermanentDesignationOccurrence:
    object_id: str
    card_ref: str
    logical_object_id: str
    controller: str
    types: tuple[str, ...]
    subtypes: tuple[str, ...]
    designation: str
    value: int | None = None

    def __post_init__(self):
        for value in (self.object_id, self.card_ref, self.logical_object_id, self.controller):
            if type(value) is not str or not value:
                raise ValueError('Designation occurrence requires sealed public identity')
        for values in (self.types, self.subtypes):
            if (type(values) is not tuple or any(type(v) is not str or not v or v != v.casefold() for v in values)
                    or values != tuple(sorted(set(values)))):
                raise ValueError('Designation characteristic vocabulary must be canonical')
        if self.designation not in {'monstrous', 'renowned'}:
            raise ValueError('Designation occurrence kind is unknown')
        if self.designation == 'monstrous':
            if type(self.value) is not int or self.value < 0:
                raise ValueError('Monstrous occurrence value must be nonnegative')
        elif self.value is not None:
            raise ValueError('Renowned occurrence has no numeric value')

    @property
    def event(self):
        return MONSTROUS_EVENT if self.designation == 'monstrous' else RENOWNED_EVENT

    def context(self):
        return {'schema_version': 1, 'object_id': self.object_id, 'card': self.card_ref,
                'object': self.card_ref, 'logical_object_id': self.logical_object_id,
                'controller': self.controller, 'types': list(self.types), 'subtypes': list(self.subtypes),
                'designation': self.designation, 'value': self.value}
