from __future__ import annotations

"""Immutable front-ability authority for a fixed-cost transformed cast."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re

from .replacement.immutable import FrozenMap
from .util import stable_json


DISTURB_CAPABILITY = 'casting.lifecycle.disturb_fixed_mana'
DISTURB_HANDLER = 'casting.cost.disturb.fixed-mana.v1'
_MANA_KEYS = frozenset(('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'))


@dataclass(frozen=True, slots=True)
class FixedManaDisturbSpec:
    instance_id: str
    front_face: str
    back_face: str
    requirements: FrozenMap
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.instance_id) is not str
                or re.fullmatch(r'[A-Za-z0-9:_-]+', self.instance_id) is None
                or any(type(name) is not str or not name.strip() for name in (self.front_face, self.back_face))
                or self.front_face.casefold() == self.back_face.casefold()
                or not isinstance(self.requirements, Mapping) or set(self.requirements) != _MANA_KEYS
                or any(type(n) is not int or n < 0 for n in self.requirements.values())):
            raise ValueError('Disturb requires distinct source faces and one fixed ordinary mana declaration')
        if not isinstance(self.requirements, FrozenMap):
            object.__setattr__(self, 'requirements', FrozenMap(self.requirements))

    def to_dict(self):
        return {'schema_version': self.schema_version, 'instance_id': self.instance_id,
            'front_face': self.front_face, 'back_face': self.back_face, 'requirements': dict(self.requirements)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id', 'front_face', 'back_face', 'requirements'}:
            raise ValueError('Disturb declaration fields are incomplete or unknown')
        return cls(**dict(value))

    @property
    def fingerprint(self):
        return hashlib.sha256(stable_json(self.to_dict()).encode('utf-8')).hexdigest()
