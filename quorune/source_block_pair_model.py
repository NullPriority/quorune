from __future__ import annotations

"""Two exact battlefield incarnations in one until-end-of-turn blocking rule."""

from dataclasses import dataclass
from typing import Mapping

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError, ContinuousObjectIdentity
from .replacement.immutable import immutable_fingerprint

SOURCE_BLOCK_PAIR_KIND = 'source_block_pair'
SOURCE_BLOCK_PAIR_EFFECT_KIND = 'resolution_source_block_pair'
SOURCE_BLOCK_PAIR_CAPABILITY = 'combat.declaration.source_block_pair'


@dataclass(frozen=True, slots=True)
class SourceBlockPairSpec:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Source-specific blocking version is closed')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'kind': SOURCE_BLOCK_PAIR_KIND}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind'} or value['kind'] != SOURCE_BLOCK_PAIR_KIND:
            raise ValueError('Source-specific blocking descriptor fields are closed')
        return cls(value['schema_version'])


@dataclass(frozen=True, slots=True)
class ResolvedSourceBlockPair:
    effect_id: str
    source_id: str
    timestamp: int
    attacker: ContinuousObjectIdentity
    blocker: ContinuousObjectIdentity
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.effect_id, self.source_id)):
            raise ContinuousEffectError('Source-specific blocking identity is malformed')
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ContinuousEffectError('Source-specific blocking timestamp is malformed')
        if not isinstance(self.attacker, ContinuousObjectIdentity) or not isinstance(self.blocker, ContinuousObjectIdentity):
            raise ContinuousEffectError('Source-specific blocking requires two typed incarnations')
        if self.source_id != self.attacker.object_id:
            raise ContinuousEffectError('Source-specific attacker must be the captured source')
        if self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN:
            raise ContinuousEffectError('Source-specific blocking expires at end of turn')

    def to_dict(self):
        return {'schema_version': 1, 'effect_kind': SOURCE_BLOCK_PAIR_EFFECT_KIND, 'effect_id': self.effect_id,
            'source_id': self.source_id, 'timestamp': self.timestamp, 'attacker': self.attacker.to_dict(),
            'blocker': self.blocker.to_dict(), 'duration': self.duration.value}

    @classmethod
    def from_dict(cls, value):
        fields = {'schema_version', 'effect_kind', 'effect_id', 'source_id', 'timestamp', 'attacker', 'blocker', 'duration'}
        if (not isinstance(value, Mapping) or set(value) != fields or value['effect_kind'] != SOURCE_BLOCK_PAIR_EFFECT_KIND
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ContinuousEffectError('Source-specific blocking journal fields are closed')
        return cls(value['effect_id'], value['source_id'], value['timestamp'],
            ContinuousObjectIdentity.from_dict(value['attacker']), ContinuousObjectIdentity.from_dict(value['blocker']),
            ContinuousEffectDuration(value['duration']))

    @property
    def fingerprint(self):
        return immutable_fingerprint(self.to_dict())
