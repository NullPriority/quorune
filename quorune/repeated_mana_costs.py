from __future__ import annotations

"""Immutable source-pinned repeated ordinary-mana additional payments."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re

from .replacement.immutable import FrozenMap
from .util import stable_json


_MANA_KEYS = frozenset({'GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'})


def validate_repeated_mana_payment(value):
    if (not isinstance(value, Mapping)
            or set(value) != {'schema_version', 'instance_id', 'cost_fingerprint', 'count'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['instance_id']) is not str
            or re.fullmatch(r'[A-Za-z0-9:_-]+', value['instance_id']) is None
            or type(value['cost_fingerprint']) is not str
            or re.fullmatch(r'[0-9a-f]{64}', value['cost_fingerprint']) is None
            or type(value['count']) is not int or value['count'] < 1):
        raise ValueError('Repeated additional mana payment is malformed')
    return dict(value)


@dataclass(frozen=True, slots=True)
class FixedRepeatedManaCostSpec:
    instance_id: str
    requirements: FrozenMap
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.instance_id) is not str
                or re.fullmatch(r'[A-Za-z0-9:_-]+', self.instance_id) is None
                or not isinstance(self.requirements, Mapping)
                or set(self.requirements) != _MANA_KEYS
                or any(type(n) is not int or n < 0 for n in self.requirements.values())
                or not any(self.requirements.values())):
            raise ValueError('A repeated cost needs one positive fixed ordinary-mana instance')
        object.__setattr__(self, 'requirements', FrozenMap(self.requirements))

    def to_dict(self):
        return {'schema_version': self.schema_version, 'instance_id': self.instance_id,
            'requirements': dict(self.requirements)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id', 'requirements'}:
            raise ValueError('Repeated additional mana cost fields are malformed')
        return cls(**dict(value))

    @property
    def fingerprint(self):
        return hashlib.sha256(stable_json(self.to_dict()).encode('utf-8')).hexdigest()

    def payment_fact(self, count):
        return validate_repeated_mana_payment({'schema_version': 1, 'instance_id': self.instance_id,
            'cost_fingerprint': self.fingerprint, 'count': count})
