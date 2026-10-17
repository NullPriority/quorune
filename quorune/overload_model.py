from __future__ import annotations

"""A fixed Overload price bound to normal and overloaded typed spell bodies."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re

from .replacement.immutable import FrozenMap
from .util import stable_json


OVERLOAD_CAPABILITY = 'casting.alternative_cost.overload_fixed_mana'
OVERLOAD_FIELD = 'overload'
OVERLOAD_BOUNDARY = 'fixed_overload_cast_variant'
OVERLOAD_NORMAL = 'overload_body_normal'
OVERLOAD_PAID = 'overload_body_paid'
_MANA_KEYS = frozenset({'GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'})


def overload_body_fingerprint(effects, target_schema):
    return hashlib.sha256(stable_json({'effects': [dict(effect) for effect in effects],
        'target_schema': dict(target_schema) if target_schema is not None else None}).encode('utf-8')).hexdigest()


@dataclass(frozen=True, slots=True)
class FixedManaOverloadSpec:
    instance_id: str
    requirements: FrozenMap
    normal_fingerprint: str
    overloaded_fingerprint: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.instance_id) is not str or re.fullmatch(r'[A-Za-z0-9:_-]+', self.instance_id) is None
                or not isinstance(self.requirements, Mapping) or set(self.requirements) != _MANA_KEYS
                or any(type(value) is not int or value < 0 for value in self.requirements.values())
                or any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None
                    for value in (self.normal_fingerprint, self.overloaded_fingerprint))
                or self.normal_fingerprint == self.overloaded_fingerprint):
            raise ValueError('Overload requires a fixed price and two distinct correlated spell bodies')
        object.__setattr__(self, 'requirements', FrozenMap(self.requirements))

    def to_dict(self):
        return {'schema_version': self.schema_version, 'instance_id': self.instance_id,
            'requirements': dict(self.requirements), 'normal_fingerprint': self.normal_fingerprint,
            'overloaded_fingerprint': self.overloaded_fingerprint}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id', 'requirements',
                'normal_fingerprint', 'overloaded_fingerprint'}):
            raise ValueError('Overload declaration fields are closed')
        return cls(**dict(value))
