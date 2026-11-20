"""Closed Demonstrate choice state over the existing immutable cast-spell snapshot."""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from .event_spell_copy_model import EventSpellCopySource


class DemonstrateStage(str, Enum):
    SELF_COPY = 'optional_self_copy'
    OPPONENT_COPY = 'required_opponent_copy'


@dataclass(frozen=True, slots=True)
class DemonstrateState:
    actor: str
    trigger_ref: str
    source: EventSpellCopySource
    stage: DemonstrateStage = DemonstrateStage.SELF_COPY
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or any(type(value) is not str or not value for value in (self.actor, self.trigger_ref))
                or not isinstance(self.source, EventSpellCopySource)
                or not isinstance(self.stage, DemonstrateStage)
                or self.source.item().kind != 'spell'
                or self.trigger_ref == self.source.item().ref):
            raise ValueError('Demonstrate requires its current actor, cast-spell snapshot and distinct trigger')
    def after_committed_self_copy(self):
        """Only enqueue this stage after the canonical copy instruction completes."""
        if self.stage is not DemonstrateStage.SELF_COPY:
            raise ValueError('Demonstrate self-copy choice has already completed')
        return type(self)(self.actor, self.trigger_ref, self.source, DemonstrateStage.OPPONENT_COPY)

    def opponents(self, active_seats):
        return tuple(seat for seat in active_seats if seat != self.actor)

    def to_dict(self):
        return {'schema_version':1, 'actor':self.actor, 'trigger_ref':self.trigger_ref,
            'source':self.source.to_dict(), 'stage':self.stage.value}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
                'schema_version','actor','trigger_ref','source','stage'}:
            raise ValueError('Demonstrate state fields are closed')
        return cls(value['actor'], value['trigger_ref'], EventSpellCopySource.from_dict(value['source']),
            DemonstrateStage(value['stage']), value['schema_version'])
