from __future__ import annotations

"""Immutable fixed mana Emerge declaration; no payment or state mutation."""

from dataclasses import dataclass
import hashlib
import re
from collections.abc import Mapping

from .replacement.immutable import FrozenMap
from .util import stable_json


EMERGE_CAPABILITY = 'casting.alternative_cost.emerge_fixed_mana'
EMERGE_HANDLER = 'casting.cost.emerge.fixed-mana.v1'
EMERGE_FIELD = 'emerge'
_MANA_KEYS = frozenset(('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'))


@dataclass(frozen=True, slots=True)
class FixedManaEmergeSpec:
    instance_id: str
    requirements: FrozenMap
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.instance_id) is not str
                or re.fullmatch(r'[A-Za-z0-9:_-]+', self.instance_id) is None
                or not isinstance(self.requirements, Mapping)
                or set(self.requirements) != _MANA_KEYS
                or any(type(n) is not int or n < 0 for n in self.requirements.values())):
            raise ValueError('Emerge requires one closed fixed ordinary mana declaration')
        if not isinstance(self.requirements, FrozenMap):
            object.__setattr__(self, 'requirements', FrozenMap(self.requirements))

    def to_dict(self):
        return {'schema_version': self.schema_version, 'instance_id': self.instance_id,
            'requirements': dict(self.requirements)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id', 'requirements'}:
            raise ValueError('Emerge declaration fields are incomplete or unknown')
        return cls(**dict(value))

    @property
    def fingerprint(self):
        return hashlib.sha256(stable_json(self.to_dict()).encode('utf-8')).hexdigest()

    def reduce_total(self, requirements, sacrificed_mana_value):
        """Reduce the supplied total after increases; preserve every color."""
        if (type(sacrificed_mana_value) is not int or sacrificed_mana_value < 0
                or not isinstance(requirements, Mapping) or set(requirements) != _MANA_KEYS
                or any(type(n) is not int or n < 0 for n in requirements.values())):
            raise ValueError('Emerge reduction requires exact current mana value and total cost')
        result = dict(requirements)
        result['GENERIC'] = max(0, result['GENERIC'] - sacrificed_mana_value)
        return result
