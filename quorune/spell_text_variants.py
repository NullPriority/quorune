from __future__ import annotations

"""Closed identities and immutable values shared by paid spell-text variants."""

from collections.abc import Mapping
from enum import Enum
import hashlib
import re

from .replacement.immutable import FrozenMap
from .util import stable_json


class SpellTextVariantKind(str, Enum):
    OVERLOAD = 'overload'
    CLEAVE = 'cleave'

    @property
    def capability(self):
        return 'casting.alternative_cost.' + self.value + '_fixed_mana'

    @property
    def boundary(self):
        return 'fixed_' + self.value + '_cast_variant'

    @property
    def normal_role(self):
        return self.value + '_body_normal'

    @property
    def paid_role(self):
        return self.value + '_body_paid'


def spell_body_fingerprint(effects, target_schema):
    return hashlib.sha256(stable_json({'effects': [dict(effect) for effect in effects],
        'target_schema': dict(target_schema) if target_schema is not None else None}).encode('utf-8')).hexdigest()


def validated_variant_mana(instance_id, requirements, normal_fingerprint, paid_fingerprint, schema_version):
    if (type(schema_version) is not int or schema_version != 1
            or type(instance_id) is not str or re.fullmatch(r'[A-Za-z0-9:_-]+', instance_id) is None
            or not isinstance(requirements, Mapping) or set(requirements) != {'GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'}
            or any(type(value) is not int or value < 0 for value in requirements.values())
            or any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None
                for value in (normal_fingerprint, paid_fingerprint))
            or normal_fingerprint == paid_fingerprint):
        raise ValueError('A paid text variant requires a fixed price and two distinct correlated spell bodies')
    return FrozenMap(requirements)
