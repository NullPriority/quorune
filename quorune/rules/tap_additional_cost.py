from __future__ import annotations

"""Closed fixed battlefield tap prices in the existing casting-cost algebra."""

from collections.abc import Mapping
from dataclasses import dataclass
from .casting_additional_costs import AdditionalCostError

TAP_ADDITIONAL_COST_KIND='fixed_tap'
TAP_ADDITIONAL_COST_CAPABILITY='casting.additional_cost.fixed_tap'
PAYMENT_COMPOSITION_CAPABILITY='casting.additional_cost.payment_keyword_composition'
PAYMENT_COMPOSITION_MECHANIC='additional-cost-payment-keyword-composition'
_TYPES=frozenset({'artifact','battle','creature','enchantment','land','planeswalker'})


@dataclass(frozen=True,slots=True)
class FixedTapAdditionalCost:
    count: int
    permanent_types: tuple[str,...]
    schema_version: int=1
    kind: str=TAP_ADDITIONAL_COST_KIND

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1 or self.kind!=TAP_ADDITIONAL_COST_KIND
                or type(self.count) is not int or self.count<1 or type(self.permanent_types) is not tuple
                or not self.permanent_types or any(type(t) is not str for t in self.permanent_types)
                or tuple(sorted(set(self.permanent_types)))!=self.permanent_types or not set(self.permanent_types)<=_TYPES):
            raise AdditionalCostError('Fixed tap price requires a positive count and canonical permanent type union')

    def to_descriptor(self):return {'schema_version':1,'kind':self.kind,'count':self.count,'permanent_types':list(self.permanent_types)}

    @classmethod
    def from_descriptor(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','kind','count','permanent_types'}
                or type(value['permanent_types']) is not list):raise AdditionalCostError('Fixed tap cost fields are malformed')
        return cls(value['count'],tuple(value['permanent_types']),value['schema_version'],value['kind'])


def fixed_tap_additional_cost(value):
    if not isinstance(value,Mapping):raise AdditionalCostError('Additional costs must be objects')
    return FixedTapAdditionalCost.from_descriptor(value) if value.get('kind')==TAP_ADDITIONAL_COST_KIND else None


def fixed_tap_additional_cost_node_capabilities(*,cost_schema):
    if not isinstance(cost_schema,Mapping) or set(cost_schema)!={'additional_costs'}:return ()
    costs=cost_schema['additional_costs']
    if type(costs) is not list or len(costs)!=1:return ()
    try:cost=fixed_tap_additional_cost(costs[0])
    except (AdditionalCostError,TypeError):return ()
    return (TAP_ADDITIONAL_COST_CAPABILITY,) if cost is not None else ()
