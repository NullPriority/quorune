from __future__ import annotations

"""Closed fixed nonmana Morph prices, separate from historical mana schemas."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib

from .creature_subtypes import canonical_creature_subtype
from .util import stable_json


NONMANA_MORPH_CAPABILITY = 'casting.morph.fixed_nonmana'
NONMANA_MORPH_HANDLER = 'casting.morph.fixed-nonmana.v1'
_MANA_FIELDS = ('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C')


class MorphObjectCostKind(str, Enum):
    LIFE = 'life'
    REVEAL = 'reveal'
    DISCARD = 'discard'
    RETURN = 'return'


@dataclass(frozen=True, slots=True)
class FixedMorphObjectCost:
    kind: MorphObjectCostKind
    count: int = 1
    subtype: str | None = None
    color: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if (not isinstance(self.kind, MorphObjectCostKind) or type(self.schema_version) is not int
                or self.schema_version != 1 or type(self.count) is not int or self.count < 1):
            raise ValueError('Morph object cost requires a closed kind and positive count')
        if self.kind is MorphObjectCostKind.LIFE:
            valid = self.count <= 99 and self.subtype is None and self.color is None
        elif self.kind is MorphObjectCostKind.REVEAL:
            valid = self.count == 1 and self.subtype is None and self.color in {'W', 'U', 'B', 'R', 'G'}
        elif self.kind is MorphObjectCostKind.DISCARD:
            valid = self.count == 1 and self.color is None and (self.subtype is None
                or type(self.subtype) is str and canonical_creature_subtype(self.subtype) == self.subtype)
        else:
            valid = self.count <= 2 and self.color is None and type(self.subtype) is str and (
                self.subtype in {'plains', 'island', 'swamp', 'mountain', 'forest'}
                or canonical_creature_subtype(self.subtype) == self.subtype)
        if not valid:
            raise ValueError('Morph object cost is outside the represented fixed resource family')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'kind': self.kind.value, 'count': self.count,
            'subtype': self.subtype, 'color': self.color}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind', 'count', 'subtype', 'color'}:
            raise ValueError('Morph object-cost fields are closed')
        return cls(MorphObjectCostKind(value['kind']), value['count'], value['subtype'], value['color'], value['schema_version'])

    @property
    def cost_text(self):
        if self.kind is MorphObjectCostKind.LIFE:
            return f'Pay {self.count} life'
        if self.kind is MorphObjectCostKind.REVEAL:
            name = {'W': 'white', 'U': 'blue', 'B': 'black', 'R': 'red', 'G': 'green'}[self.color]
            return f'Reveal a {name} card in your hand'
        if self.kind is MorphObjectCostKind.DISCARD:
            return 'Discard a ' + (self.subtype + ' ' if self.subtype else '') + 'card'
        return f'Return {self.count} controlled {self.subtype} permanent(s) to their owners hands'


@dataclass(frozen=True, slots=True)
class FixedNonmanaMorphSpec:
    nonmana_cost: FixedMorphObjectCost
    schema_version: int = 3
    method: str = 'morph'

    def __post_init__(self):
        if (not isinstance(self.nonmana_cost, FixedMorphObjectCost) or type(self.schema_version) is not int
                or self.schema_version != 3 or self.method != 'morph'):
            raise ValueError('Nonmana Morph requires one version-three Morph declaration')

    @property
    def requirements(self):
        return (0,) * 7

    @property
    def requirements_dict(self):
        return dict.fromkeys(_MANA_FIELDS, 0)

    @property
    def mana_cost_options(self):
        return ()

    @property
    def cost_text(self):
        return self.nonmana_cost.cost_text

    @property
    def fingerprint(self):
        return hashlib.sha256(stable_json(self.to_dict()).encode('utf-8')).hexdigest()

    def to_dict(self):
        return {'schema_version': self.schema_version, 'requirements': self.requirements_dict,
            'nonmana_cost': self.nonmana_cost.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'requirements', 'nonmana_cost'}
                or not isinstance(value['requirements'], Mapping)
                or value['requirements'] != dict.fromkeys(_MANA_FIELDS, 0)
                or any(type(v) is not int for v in value['requirements'].values())):
            raise ValueError('Nonmana Morph has a closed zero-mana version-three shape')
        return cls(FixedMorphObjectCost.from_dict(value['nonmana_cost']), value['schema_version'])
