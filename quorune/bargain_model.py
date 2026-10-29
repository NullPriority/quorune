"""A printed Bargain instance and its typed sacrifice declaration."""
from collections.abc import Mapping
from dataclasses import dataclass
from .declared_cast_cost_model import DeclaredCastCostChoice, DeclaredCastCostFacts, DeclaredCastCostKind
from .sacrifice_union_model import SacrificePredicateUnion, controlled_permanent_predicate

BARGAIN_CAPABILITY = 'casting.additional_cost.bargain'
BARGAIN_HANDLER = 'casting.bargain.optional-sacrifice.v1'
BARGAIN_INSTANCE = 'front:bargain:1'


@dataclass(frozen=True, slots=True)
class FixedBargainSpec:
    instance_id: str = BARGAIN_INSTANCE
    schema_version: int = 1

    def __post_init__(self):
        DeclaredCastCostChoice(DeclaredCastCostKind.BARGAIN, self.instance_id, False)
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Bargain declaration version is unsupported')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'instance_id': self.instance_id}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id'}:
            raise ValueError('Bargain declaration has a closed schema')
        return cls(**dict(value))

    def declaration(self, chosen):
        return DeclaredCastCostFacts((DeclaredCastCostChoice(DeclaredCastCostKind.BARGAIN, self.instance_id, chosen),))

    def price_union(self):
        return SacrificePredicateUnion((controlled_permanent_predicate(types_any=('artifact', 'enchantment')),
            controlled_permanent_predicate(token=True)))
