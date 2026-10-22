from __future__ import annotations

"""Immutable printed characteristics for the hand-only Fuse casting choice."""

from dataclasses import dataclass
from collections.abc import Mapping
import re

FUSE_CAPABILITY = 'casting.method.fuse'
FUSE_HANDLER = 'ability.static.fuse.v1'
FUSE_BOUNDARY = 'fuse-complete-split-source'
FUSE_TEMPLATE = 'fused-split-spell-v1'
FUSE_ANNOTATION = 'fused_spell_characteristics'


@dataclass(frozen=True, slots=True)
class FuseHalf:
    face_id: str
    name: str
    mana_cost: str
    type_line: str
    colors: tuple[str, ...]

    def __post_init__(self):
        if any(type(value) is not str or not value.strip() for value in
                (self.face_id, self.name, self.mana_cost, self.type_line)):
            raise ValueError('Fuse halves require printed identity and characteristics')
        if not re.fullmatch(r'(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+', self.mana_cost):
            raise ValueError('This Fuse production requires fixed printed mana costs')
        if self.type_line not in {'Instant', 'Sorcery'}:
            raise ValueError('Fuse halves must be instant or sorcery spells')
        object.__setattr__(self, 'colors', tuple(self.colors))
        if any(c not in 'WUBRG' or len(c) != 1 for c in self.colors) or len(set(self.colors)) != len(self.colors):
            raise ValueError('Fuse half colors must be unique printed colors')

    def to_dict(self):
        return dict(face_id=self.face_id, name=self.name, mana_cost=self.mana_cost,
            type_line=self.type_line, colors=list(self.colors))

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, Mapping) or set(raw) != {'face_id', 'name', 'mana_cost', 'type_line', 'colors'}:
            raise ValueError('Fuse half has a closed schema')
        if not isinstance(raw['colors'], (tuple, list)):
            raise ValueError('Fuse colors must be an array')
        return cls(**raw)


@dataclass(frozen=True, slots=True)
class FuseSpec:
    left: FuseHalf
    right: FuseHalf

    def __post_init__(self):
        if not isinstance(self.left, FuseHalf) or not isinstance(self.right, FuseHalf):
            raise ValueError('Fuse requires two typed halves')
        if self.left.face_id == self.right.face_id or self.left.name == self.right.name:
            raise ValueError('Fuse halves require distinct identities')

    @property
    def ability_id(self):
        return f'spell:{self.left.face_id}:fuse'

    def to_dict(self):
        return {'schema_version': 1, 'left': self.left.to_dict(), 'right': self.right.to_dict()}

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, Mapping) or set(raw) != {'schema_version', 'left', 'right'}:
            raise ValueError('Fuse declaration has a closed schema')
        if type(raw['schema_version']) is not int or raw['schema_version'] != 1:
            raise ValueError('Unsupported Fuse declaration version')
        return cls(FuseHalf.from_dict(raw['left']), FuseHalf.from_dict(raw['right']))

    def characteristic_values(self):
        halves = (self.left, self.right)
        cost = ''.join(h.mana_cost for h in halves)
        return {'name': ' // '.join(h.name for h in halves), 'mana_cost': cost,
            'mana_value': sum(int(v) if v.isdigit() else 1 for v in re.findall(r'\{([^}]+)\}', cost)),
            'type_line': ' '.join(dict.fromkeys(h.type_line for h in halves)),
            'colors': [c for c in 'WUBRG' if any(c in h.colors for h in halves)]}
