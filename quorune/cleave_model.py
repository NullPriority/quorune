from __future__ import annotations

"""One fixed Cleave price and the two bracket-derived spell bodies."""

from collections.abc import Mapping
from dataclasses import dataclass
from .replacement.immutable import FrozenMap
from .spell_text_variants import SpellTextVariantKind, validated_variant_mana, spell_body_fingerprint


CLEAVE_CAPABILITY = SpellTextVariantKind.CLEAVE.capability
CLEAVE_FIELD = 'cleave'
CLEAVE_BOUNDARY = SpellTextVariantKind.CLEAVE.boundary
CLEAVE_NORMAL = SpellTextVariantKind.CLEAVE.normal_role
CLEAVE_PAID = SpellTextVariantKind.CLEAVE.paid_role


@dataclass(frozen=True, slots=True)
class FixedManaCleaveSpec:
    instance_id: str
    requirements: FrozenMap
    normal_fingerprint: str
    cleaved_fingerprint: str
    schema_version: int = 1

    def __post_init__(self):
        object.__setattr__(self, 'requirements', validated_variant_mana(self.instance_id, self.requirements,
            self.normal_fingerprint, self.cleaved_fingerprint, self.schema_version))

    def to_dict(self):
        return {'schema_version': self.schema_version, 'instance_id': self.instance_id,
            'requirements': dict(self.requirements), 'normal_fingerprint': self.normal_fingerprint,
            'cleaved_fingerprint': self.cleaved_fingerprint}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id', 'requirements',
                'normal_fingerprint', 'cleaved_fingerprint'}:
            raise ValueError('Cleave declaration fields are closed')
        return cls(**dict(value))
