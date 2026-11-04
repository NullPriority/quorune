"""Typed fixed ordinary/hybrid cost of one intrinsic Mutate ability."""
from collections.abc import Mapping
from dataclasses import dataclass
from .fixed_mana_model import FixedManaCostError,fixed_mana_variants
from .replacement.immutable import thaw_value

MUTATE_CAPABILITY='casting.mutate.fixed_mana'
MUTATE_HANDLER='ability.static.mutate.v1'
MUTATE_PAYMENT_FIELD='mutate_payment'
MUTATE_EVENT='permanent.mutated'


@dataclass(frozen=True,slots=True)
class MutateSpec:
    mana_cost:str
    schema_version:int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:
            raise ValueError('Unsupported Mutate cost schema')
        try:fixed_mana_variants(self.mana_cost)
        except (TypeError,FixedManaCostError) as exc:
            raise ValueError('Mutate requires a fixed ordinary or hybrid mana cost') from exc

    @property
    def capability_id(self):return MUTATE_CAPABILITY

    @property
    def mana_variants(self):return fixed_mana_variants(self.mana_cost)

    def to_dict(self):return {'schema_version':1,'mana_cost':self.mana_cost}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','mana_cost'}:
            raise ValueError('Mutate cost has a closed typed schema')
        return cls(value['mana_cost'],value['schema_version'])

    def target_schema(self):
        return {'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],
            'subtypes_none':['human'],'owner_relation':'spell_owner','count':1}

    def cast_cost_options(self):
        variants=self.mana_variants
        return tuple({'id':f'mutate-hybrid-{index}' if len(variants)>1 else 'mutate',
            'kind':'alternate','label':f'Mutate {self.mana_cost}',
            'requirements':thaw_value(requirements),MUTATE_PAYMENT_FIELD:self.to_dict(),
            'target_schema':self.target_schema(),
            'effects':[{'op':'mutate_merge','target':'$target.0'}]}
            for index,requirements in enumerate(variants,start=1))


