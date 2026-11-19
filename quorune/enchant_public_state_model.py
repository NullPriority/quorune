"""Closed live Enchant qualifications projected into existing target predicates."""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from .object_predicate import PermanentStatePredicateSpec


ENCHANT_PUBLIC_STATE_CAPABILITY = 'attachment.aura.public_state_restriction'
OTHER_AURA_ATTACHED_PREDICATE = 'other_aura_attached'


class EnchantPublicStateKind(str, Enum):
    MODIFIED_CREATURE = 'modified_creature'
    NONBASIC_LAND = 'nonbasic_land'
    CREATURE_WITH_OTHER_AURA = 'creature_with_other_aura'


@dataclass(frozen=True, slots=True)
class EnchantPublicStateSpec:
    kind: EnchantPublicStateKind
    schema_version: int = 1

    def __post_init__(self):
        if (not isinstance(self.kind, EnchantPublicStateKind)
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError('Public-state Enchant requires one typed qualification and explicit version one')

    def to_dict(self):
        return {'kind': self.kind.value, 'schema_version': self.schema_version}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version'}
                or type(value.get('kind')) is not str):
            raise ValueError('Public-state Enchant qualification fields are closed')
        return cls(EnchantPublicStateKind(value['kind']), value['schema_version'])

    @property
    def types_all(self):
        return ('land',) if self.kind is EnchantPublicStateKind.NONBASIC_LAND else ('creature',)

    def target_fields(self):
        fields = {'types_all': list(self.types_all)}
        if self.kind is EnchantPublicStateKind.MODIFIED_CREATURE:
            fields['state_predicate'] = PermanentStatePredicateSpec(modified=True).to_dict()
        elif self.kind is EnchantPublicStateKind.NONBASIC_LAND:
            fields['supertypes_none'] = ['basic']
        else:
            fields['predicate'] = OTHER_AURA_ATTACHED_PREDICATE
        return fields
