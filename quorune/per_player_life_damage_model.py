"""Versioned life fractions and sealed player shares for simultaneous damage."""
from collections.abc import Mapping
from dataclasses import dataclass

from .announced_damage import AllocatedDamageRecipient
from .fixed_damage_set_model import PlayerDamageRelation
from .fractional_quantity_model import HalfQuantitySpec
from .per_player_query_damage_model import BoundPerPlayerQueryDamage

PER_PLAYER_LIFE_DAMAGE_KIND = 'per_player_current_life_damage_amount'
BOUND_PER_PLAYER_LIFE_DAMAGE_KIND = 'bound_per_player_current_life_damage_amount'
PER_PLAYER_LIFE_DAMAGE_CAPABILITY = 'damage.batch.per_player_current_life_fraction'
PER_PLAYER_LIFE_DAMAGE_MECHANIC = 'per-player-current-life-fraction-damage'


@dataclass(frozen=True, slots=True)
class PerPlayerLifeDamageSpec:
    fraction: HalfQuantitySpec
    relation: PlayerDamageRelation
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.fraction, HalfQuantitySpec)
                or not isinstance(self.relation, PlayerDamageRelation)):
            raise ValueError('Per-player life damage requires a versioned fraction and player relation')

    def to_dict(self):
        return {'kind': PER_PLAYER_LIFE_DAMAGE_KIND, 'schema_version': self.schema_version,
                'relation': self.relation.value, 'fraction': self.fraction.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'relation', 'fraction'}
                or value['kind'] != PER_PLAYER_LIFE_DAMAGE_KIND):
            raise ValueError('Per-player life damage fields are closed')
        return cls(HalfQuantitySpec.from_dict(value['fraction']), PlayerDamageRelation(value['relation']), value['schema_version'])


@dataclass(frozen=True, slots=True)
class BoundPerPlayerLifeDamage:
    controller: str
    relation: PlayerDamageRelation
    recipients: tuple[AllocatedDamageRecipient, ...]
    schema_version: int = 1

    def __post_init__(self):
        # Reuse the established distinct-player/positive-share authority while
        # retaining a separate persisted kind for this quantity's provenance.
        BoundPerPlayerQueryDamage(self.controller, self.relation, self.recipients, self.schema_version)

    @property
    def total(self):
        return sum(recipient.amount for recipient in self.recipients)

    def to_dict(self):
        return {'kind': BOUND_PER_PLAYER_LIFE_DAMAGE_KIND, 'schema_version': self.schema_version,
                'controller': self.controller, 'relation': self.relation.value,
                'recipients': [recipient.to_dict() for recipient in self.recipients]}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'controller', 'relation', 'recipients'}
                or value['kind'] != BOUND_PER_PLAYER_LIFE_DAMAGE_KIND
                or not isinstance(value['recipients'], (list, tuple))):
            raise ValueError('Bound life-damage fields are closed')
        return cls(value['controller'], PlayerDamageRelation(value['relation']),
            tuple(AllocatedDamageRecipient.from_dict(row) for row in value['recipients']), value['schema_version'])
