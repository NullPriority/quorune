from __future__ import annotations

"""Incarnation-locked CR 509 permissions created by resolution."""

from dataclasses import dataclass
from typing import Mapping

from .block_capacity_model import BlockCapacitySpec
from .continuous_effect_model import (
    ContinuousEffectDuration, ContinuousEffectError, ContinuousObjectIdentity,
)
from .replacement.immutable import immutable_fingerprint


BLOCK_CAPACITY_RULE_KIND = 'resolution_block_capacity'
TEMPORARY_BLOCK_CAPACITY = 'combat.block.capacity.temporary'
TEMPORARY_BLOCK_CAPACITY_MECHANIC = 'fixed-temporary-block-capacity'


@dataclass(frozen=True, slots=True)
class ResolvedBlockCapacityRule:
    effect_id: str
    source_id: str
    timestamp: int
    subject: ContinuousObjectIdentity
    capacity: BlockCapacitySpec
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.effect_id, self.source_id)):
            raise ContinuousEffectError('Blocking rule identity is malformed')
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ContinuousEffectError('Blocking rule timestamp is malformed')
        if not isinstance(self.subject, ContinuousObjectIdentity) or not isinstance(self.capacity, BlockCapacitySpec):
            raise ContinuousEffectError('Blocking rule requires a locked object and closed capacity')
        if self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN:
            raise ContinuousEffectError('Resolved blocking capacity expires at end of turn')

    def to_dict(self):
        return {'effect_kind': BLOCK_CAPACITY_RULE_KIND, 'effect_id': self.effect_id,
            'source_id': self.source_id, 'timestamp': self.timestamp,
            'subject': self.subject.to_dict(), 'capacity': self.capacity.to_dict(),
            'duration': self.duration.value}

    @classmethod
    def from_dict(cls, value: Mapping):
        fields = {'effect_kind', 'effect_id', 'source_id', 'timestamp', 'subject', 'capacity', 'duration'}
        if not isinstance(value, Mapping) or set(value) != fields or value['effect_kind'] != BLOCK_CAPACITY_RULE_KIND:
            raise ContinuousEffectError('Resolved blocking capacity has a closed schema')
        return cls(effect_id=value['effect_id'], source_id=value['source_id'], timestamp=value['timestamp'],
            subject=ContinuousObjectIdentity.from_dict(value['subject']),
            capacity=BlockCapacitySpec.from_dict(value['capacity']),
            duration=ContinuousEffectDuration(value['duration']))

    @property
    def fingerprint(self):
        return immutable_fingerprint(self.to_dict())
