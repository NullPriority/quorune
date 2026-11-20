from __future__ import annotations

"""A proved single event object can be a dealer without becoming the ability source."""

from collections.abc import Mapping
from dataclasses import dataclass
from .damage_source import DamageSourceSnapshot
from .replacement.immutable import thaw_value

EVENT_DAMAGE_SOURCE_KIND = 'event_damage_source'
EVENT_DAMAGE_SOURCE_CONTEXT = 'event_damage_source_information'
EVENT_DAMAGE_SOURCE_CAPABILITY = 'damage.source.event_object_current_or_lki'
EVENT_DAMAGE_SOURCE_EVENTS = frozenset({'creature.enter', 'permanent.enter', 'creature.attacks', 'permanent.tap',
    'damage.received', 'combat.damage.received'})


@dataclass(frozen=True, slots=True)
class EventDamageSourceReference:
    event: str
    schema_version: int = 1
    kind: str = EVENT_DAMAGE_SOURCE_KIND

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or self.kind != EVENT_DAMAGE_SOURCE_KIND or type(self.event) is not str
                or self.event not in EVENT_DAMAGE_SOURCE_EVENTS):
            raise ValueError('Event damage source requires one proved public occurrence')

    def to_dict(self):
        return {'kind': self.kind, 'schema_version': 1, 'event': self.event}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'event'}:
            raise ValueError('Event damage source reference fields are closed')
        return cls(**value)


@dataclass(frozen=True, slots=True)
class EventDamageSourceInformation:
    event: str
    source_snapshot: DamageSourceSnapshot
    schema_version: int = 1
    event_id: str | None = None
    received_amount: int | None = None

    def __post_init__(self):
        EventDamageSourceReference(self.event)
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.source_snapshot, DamageSourceSnapshot)
                or self.source_snapshot.zone != 'battlefield'
                or not self.source_snapshot.object_id or not self.source_snapshot.logical_object_id
                or self.source_snapshot.logical_object_id.rpartition('@')[0] != self.source_snapshot.object_id):
            raise ValueError('Event damage source information requires its original battlefield incarnation')
        if self.event in {'damage.received', 'combat.damage.received'}:
            if type(self.event_id) is not str or not self.event_id or type(self.received_amount) is not int or self.received_amount <= 0:
                raise ValueError('Received damage dealer requires its sealed positive committed amount and event')
        elif self.event_id is not None or self.received_amount is not None:
            raise ValueError('Other dealer events cannot borrow a received damage amount')

    def to_dict(self):
        return {'schema_version': 1, 'event': self.event, 'source_snapshot': self.source_snapshot.to_dict(),
            'event_id': self.event_id, 'received_amount': self.received_amount}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'event', 'source_snapshot', 'event_id', 'received_amount'}:
            raise ValueError('Event damage source information fields are closed')
        raw = thaw_value(value)
        return cls(raw['event'], DamageSourceSnapshot.from_dict(raw['source_snapshot']), raw['schema_version'],
            raw['event_id'], raw['received_amount'])
