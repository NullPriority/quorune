from __future__ import annotations

"""One printed fixed-mana Multikicker instance and its immutable payment fact."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re

from .replacement.immutable import FrozenMap
from .util import stable_json


MULTIKICKER_CAPABILITY = 'casting.multikicker.fixed_mana'
MULTIKICKER_ENTRY_CAPABILITY = 'replacement.multikicker.named_entry_counters'
MULTIKICKER_COST_HANDLER = 'casting.multikicker.fixed-mana.v1'
MULTIKICKER_ENTRY_HANDLER = 'replacement.zone.multikicker-counters.v1'
MULTIKICKER_FIELD = 'multikicker_paid'
MULTIKICKER_COUNT_FIELD = 'multikicker_count'
_MANA_KEYS = frozenset({'GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'})


def validate_multikicker_fact(value):
    if (not isinstance(value, Mapping)
            or set(value) != {'schema_version', 'instance_id', 'cost_fingerprint', 'count'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['instance_id']) is not str
            or re.fullmatch(r'[A-Za-z0-9:_-]+', value['instance_id']) is None
            or type(value['cost_fingerprint']) is not str
            or re.fullmatch(r'[0-9a-f]{64}', value['cost_fingerprint']) is None
            or type(value['count']) is not int or value['count'] < 1):
        raise ValueError('Multikicker payment fact is malformed')
    return dict(value)


@dataclass(frozen=True, slots=True)
class FixedManaMultikickerSpec:
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
            raise ValueError('Multikicker needs one positive fixed ordinary-mana instance')
        if not isinstance(self.requirements, FrozenMap):
            object.__setattr__(self, 'requirements', FrozenMap(self.requirements))

    def to_dict(self):
        return {'schema_version': self.schema_version, 'instance_id': self.instance_id,
            'requirements': dict(self.requirements)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id', 'requirements'}:
            raise ValueError('Multikicker cost fields are malformed')
        return cls(**dict(value))

    @property
    def fingerprint(self):
        return hashlib.sha256(stable_json(self.to_dict()).encode('utf-8')).hexdigest()

    def payment_fact(self, count):
        return validate_multikicker_fact({'schema_version': 1, 'instance_id': self.instance_id,
            'cost_fingerprint': self.fingerprint, 'count': count})


@dataclass(frozen=True, slots=True)
class MultikickerEntrySpec:
    cost: FixedManaMultikickerSpec
    counter_name: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.cost, FixedManaMultikickerSpec) or type(self.counter_name) is not str
                or self.counter_name not in {'+1/+1', 'charge'}):
            raise ValueError('Multikicker entry counter is outside the closed family')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'cost': self.cost.to_dict(),
            'counter_name': self.counter_name}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'cost', 'counter_name'}:
            raise ValueError('Multikicker entry fields are malformed')
        return cls(FixedManaMultikickerSpec.from_dict(value['cost']), value['counter_name'], value['schema_version'])

    def paid_count(self, fact):
        if fact is None:
            return 0
        paid = validate_multikicker_fact(fact)
        if paid['instance_id'] != self.cost.instance_id or paid['cost_fingerprint'] != self.cost.fingerprint:
            raise ValueError('Multikicker entry does not match its paid instance')
        return paid['count']
