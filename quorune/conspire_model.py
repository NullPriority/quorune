from __future__ import annotations

"""One ordinary Conspire instance and its two paid object incarnations."""

from collections.abc import Mapping
from dataclasses import dataclass
import re


CONSPIRE_CAPABILITY = 'casting.additional_cost.conspire_fixed_tap'
CONSPIRE_HANDLER = 'casting.conspire.fixed-tap.v1'
CONSPIRE_FIELD = 'conspire_paid'
CONSPIRE_SEMANTIC_KEY = 'builtin:conspire'
_INSTANCE = re.compile(r'[A-Za-z0-9:_-]+')


def validate_conspire_fact(value):
    if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id', 'objects'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['instance_id']) is not str or _INSTANCE.fullmatch(value['instance_id']) is None
            or not isinstance(value['objects'], (list, tuple)) or len(value['objects']) != 2):
        raise ValueError('Conspire needs one paid instance and two object incarnations')
    rows = []
    for row in value['objects']:
        if (not isinstance(row, Mapping) or set(row) != {'ref', 'logical_object_id'}
                or any(type(row[key]) is not str or not row[key] for key in row)):
            raise ValueError('Conspire paid object identity is malformed')
        rows.append(dict(row))
    if len({row['ref'] for row in rows}) != 2 or len({row['logical_object_id'] for row in rows}) != 2:
        raise ValueError('Conspire requires two distinct object incarnations')
    return {'schema_version': 1, 'instance_id': value['instance_id'], 'objects': rows}


@dataclass(frozen=True, slots=True)
class FixedConspireSpec:
    instance_id: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.instance_id) is not str or _INSTANCE.fullmatch(self.instance_id) is None):
            raise ValueError('Conspire declaration identity is malformed')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'instance_id': self.instance_id}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id'}:
            raise ValueError('Conspire declaration fields are closed')
        return cls(**dict(value))

    def payment_fact(self, objects):
        return validate_conspire_fact({**self.to_dict(), 'objects': list(objects)})
