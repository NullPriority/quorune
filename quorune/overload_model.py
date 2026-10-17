from __future__ import annotations

"""A fixed Overload price bound to normal and overloaded typed spell bodies."""

from collections.abc import Mapping
from dataclasses import dataclass

from .replacement.immutable import FrozenMap
from .spell_text_variants import validated_variant_mana, spell_body_fingerprint


OVERLOAD_CAPABILITY = 'casting.alternative_cost.overload_fixed_mana'
OVERLOAD_FIELD = 'overload'
OVERLOAD_BOUNDARY = 'fixed_overload_cast_variant'
OVERLOAD_NORMAL = 'overload_body_normal'
OVERLOAD_PAID = 'overload_body_paid'
_MANA_KEYS = frozenset({'GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'})


def overload_body_fingerprint(effects, target_schema):
    return spell_body_fingerprint(effects, target_schema)


@dataclass(frozen=True, slots=True)
class FixedManaOverloadSpec:
    instance_id: str
    requirements: FrozenMap
    normal_fingerprint: str
    overloaded_fingerprint: str
    schema_version: int = 1

    def __post_init__(self):
        object.__setattr__(self, 'requirements', validated_variant_mana(self.instance_id, self.requirements,
            self.normal_fingerprint, self.overloaded_fingerprint, self.schema_version))

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
