from __future__ import annotations

"""Immutable additional-cost identity and the resulting paid-entry fact."""

from collections.abc import Mapping
from dataclasses import dataclass
import re

from .replacement.immutable import FrozenMap


PAID_ENTRY_COPY_CAPABILITY = 'casting.additional.paid_entry_copy'
PAID_ENTRY_COPY_HANDLER = 'casting.additional.paid-entry-copy.v1'
PAID_ENTRY_COPY_FIELD = 'paid_entry_copy'
PAID_ENTRY_COPY_MECHANIC = 'paid-entry-copy-offspring'
MANA_KEYS = frozenset({'GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'})


def validate_paid_entry_copy_fact(value):
    if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'keyword', 'instance_id', 'count'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['keyword'] != 'offspring' or type(value['count']) is not int or value['count'] != 1
            or type(value['instance_id']) is not str or not re.fullmatch(r'[A-Za-z0-9:_-]+', value['instance_id'])):
        raise ValueError('Paid entry copy fact is malformed or unsupported')
    return dict(value)


@dataclass(frozen=True, slots=True)
class PaidEntryCopySpec:
    instance_id: str
    requirements: FrozenMap
    keyword: str = 'offspring'
    schema_version: int = 1

    def __post_init__(self):
        validate_paid_entry_copy_fact(self.payment_fact())
        if (not isinstance(self.requirements, Mapping) or set(self.requirements) != MANA_KEYS
                or any(type(n) is not int or n < 0 for n in self.requirements.values())
                or not any(self.requirements.values())):
            raise ValueError('Paid entry copy needs a positive fixed ordinary mana cost')
        if not isinstance(self.requirements, FrozenMap):
            object.__setattr__(self, 'requirements', FrozenMap(self.requirements))

    def payment_fact(self):
        return {'schema_version': self.schema_version, 'keyword': self.keyword,
            'instance_id': self.instance_id, 'count': 1}

    def to_dict(self):
        return {'schema_version': self.schema_version, 'keyword': self.keyword,
            'instance_id': self.instance_id, 'requirements': dict(self.requirements)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'keyword', 'instance_id', 'requirements'}:
            raise ValueError('Paid entry copy cost fields are malformed')
        return cls(**dict(value))
