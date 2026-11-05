from __future__ import annotations

"""Closed damage-followup and end-of-turn destination rule descriptors."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError, ContinuousObjectIdentity


DAMAGE_DEATH_EXILE_FIELD = 'death_exile_scope'
DAMAGE_DEATH_EXILE_MECHANIC = 'damage-death-exile-followup'
DAMAGE_DEATH_EXILE_CAPABILITY = 'damage.followup.exile_on_death'
DAMAGE_DEATH_EXILE_KIND = 'resolution_damage_death_exile'


class DamageDeathExileScope(StrEnum):
    TARGET = 'target_incarnation'
    DEALT_CREATURES = 'dealt_creature_incarnations'


def damage_death_exile_scope(effect):
    if DAMAGE_DEATH_EXILE_FIELD not in effect:
        return None
    value = effect[DAMAGE_DEATH_EXILE_FIELD]
    if type(value) is not str:
        raise ValueError('Damage death exile requires a closed recipient scope')
    return DamageDeathExileScope(value)


def damage_death_exile_payload_dependencies(effect, *, coverage):
    scope = damage_death_exile_scope(effect)
    if scope is None:
        return ()
    operation = effect.get('op')
    if (DAMAGE_DEATH_EXILE_MECHANIC not in coverage or operation not in {'damage', 'damage_fixed_set'}
            or operation == 'damage_fixed_set' and scope is not DamageDeathExileScope.DEALT_CREATURES):
        raise ValueError('Damage death exile requires a closed damage instruction and recipient scope')
    return (DAMAGE_DEATH_EXILE_CAPABILITY,)


@dataclass(frozen=True, slots=True)
class ResolvedDamageDeathExile:
    effect_id: str
    source_id: str
    timestamp: int
    locked_objects: tuple[ContinuousObjectIdentity, ...]
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.effect_id, self.source_id))
                or type(self.timestamp) is not int or self.timestamp < 0
                or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN):
            raise ContinuousEffectError('Damage death exile requires exact identity and end-of-turn duration')
        if (not isinstance(self.locked_objects, tuple) or not self.locked_objects
                or any(not isinstance(item, ContinuousObjectIdentity) for item in self.locked_objects)
                or len({item.object_id for item in self.locked_objects}) != len(self.locked_objects)):
            raise ContinuousEffectError('Damage death exile requires unique typed permanent incarnations')

    def to_dict(self):
        return {'effect_kind': DAMAGE_DEATH_EXILE_KIND, 'schema_version': 1,
                'effect_id': self.effect_id, 'source_id': self.source_id, 'timestamp': self.timestamp,
                'locked_objects': [item.to_dict() for item in self.locked_objects], 'duration': self.duration.value}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
            'effect_kind', 'schema_version', 'effect_id', 'source_id', 'timestamp', 'locked_objects', 'duration',
        } or value['effect_kind'] != DAMAGE_DEATH_EXILE_KIND
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['locked_objects'], (list, tuple))):
            raise ContinuousEffectError('Damage death exile journal fields are closed')
        return cls(value['effect_id'], value['source_id'], value['timestamp'],
                   tuple(ContinuousObjectIdentity.from_dict(item) for item in value['locked_objects']),
                   ContinuousEffectDuration(value['duration']))
