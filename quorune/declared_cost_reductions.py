"""Immutable source-linked generic reductions qualified by announced costs."""
from collections.abc import Mapping
from dataclasses import dataclass
from .declared_cost_conditions import DeclaredCastCostCondition

DECLARED_COST_REDUCTION_HANDLER = 'modification.cast-cost.declared-instance.v1'
DECLARED_COST_REDUCTION_EVENT = 'cast.cost.modify'


@dataclass(frozen=True, slots=True)
class DeclaredCostReductionSpec:
    condition: DeclaredCastCostCondition
    generic_reduction: int
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.condition, DeclaredCastCostCondition)
                or type(self.generic_reduction) is not int or self.generic_reduction <= 0):
            raise ValueError('Declared-cost reductions require a linked condition and positive generic amount')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'condition': self.condition.to_dict(),
                'generic_reduction': self.generic_reduction}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'condition', 'generic_reduction'}:
            raise ValueError('Declared-cost reductions have a closed schema')
        if not isinstance(value['condition'], Mapping) or type(value['condition'].get('schema_version')) is not int:
            raise ValueError('Declared-cost reduction conditions require an integer version')
        return cls(DeclaredCastCostCondition.from_dict(value['condition']),
                   value['generic_reduction'], value['schema_version'])

    def reduction_for(self, facts):
        return self.generic_reduction if self.condition.matches(facts) else 0
