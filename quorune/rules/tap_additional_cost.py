from __future__ import annotations

"""Closed fixed battlefield tap prices in the existing casting-cost algebra."""

from collections.abc import Mapping
from dataclasses import dataclass
from .casting_additional_costs import AdditionalCostError

TAP_ADDITIONAL_COST_KIND='fixed_tap'
TAP_ADDITIONAL_COST_CAPABILITY='casting.additional_cost.fixed_tap'
COLOR_TAP_ADDITIONAL_COST_CAPABILITY = "casting.additional_cost.fixed_color_tap"
COLOR_TAP_SCHEMA_VERSION = 3
PAYMENT_COMPOSITION_CAPABILITY='casting.additional_cost.payment_keyword_composition'
PAYMENT_COMPOSITION_MECHANIC='additional-cost-payment-keyword-composition'
_TYPES=frozenset({'artifact','battle','creature','enchantment','land','planeswalker'})


@dataclass(frozen=True,slots=True)
class FixedTapAdditionalCost:
    count: int
    permanent_types: tuple[str,...]
    schema_version: int=1
    kind: str=TAP_ADDITIONAL_COST_KIND
    creature_subtypes: tuple[str,...]=()
    colors_all: tuple[str, ...] = ()

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version not in {1,2,COLOR_TAP_SCHEMA_VERSION} or self.kind!=TAP_ADDITIONAL_COST_KIND
                or type(self.count) is not int or self.count<1 or type(self.permanent_types) is not tuple
                or not self.permanent_types or any(type(t) is not str for t in self.permanent_types)
                or tuple(sorted(set(self.permanent_types)))!=self.permanent_types or not set(self.permanent_types)<=_TYPES):
            raise AdditionalCostError('Fixed tap price requires a positive count and canonical permanent type union')
        from ..creature_subtypes import canonical_creature_subtype
        if (type(self.creature_subtypes) is not tuple or any(type(s) is not str for s in self.creature_subtypes)
                or tuple(sorted(set(self.creature_subtypes)))!=self.creature_subtypes
                or any(canonical_creature_subtype(s)!=s for s in self.creature_subtypes)
                or (self.schema_version!=2 and self.creature_subtypes)
                or (self.schema_version==2 and (not self.creature_subtypes or self.permanent_types!=('creature',)))):
            raise AdditionalCostError('Subtype tap prices require a version-two creature predicate')
        if (type(self.colors_all) is not tuple or (self.schema_version != COLOR_TAP_SCHEMA_VERSION and self.colors_all)
            or (self.schema_version == COLOR_TAP_SCHEMA_VERSION and (
                self.count != 1 or self.permanent_types != ("creature",) or len(self.colors_all) != 1
                or type(self.colors_all[0]) is not str or self.colors_all[0] not in frozenset("WUBRG")
            ))):
            raise AdditionalCostError("Color tap requires one controlled creature of one current color")

    def to_descriptor(self):return {'schema_version':self.schema_version,'kind':self.kind,'count':self.count,'permanent_types':list(self.permanent_types),
        **({'creature_subtypes':list(self.creature_subtypes)} if self.schema_version==2 else {}),
        **({"colors_all": list(self.colors_all)} if self.schema_version == COLOR_TAP_SCHEMA_VERSION else {})}

    @classmethod
    def from_descriptor(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','kind','count','permanent_types',
                *({'creature_subtypes'} if value.get('schema_version')==2 else set()),
                *({"colors_all"} if value.get("schema_version") == COLOR_TAP_SCHEMA_VERSION else set())}
                or type(value['permanent_types']) is not list):raise AdditionalCostError('Fixed tap cost fields are malformed')
        if value.get('schema_version')==2 and type(value.get('creature_subtypes')) is not list:raise AdditionalCostError('Subtype tap fields are malformed')
        if value.get("schema_version") == COLOR_TAP_SCHEMA_VERSION and type(value.get("colors_all")) is not list:
            raise AdditionalCostError("Color tap fields are malformed")
        return cls(value['count'],tuple(value['permanent_types']),value['schema_version'],value['kind'],
            tuple(value.get('creature_subtypes',())), tuple(value.get("colors_all", ())))


def fixed_tap_additional_cost(value):
    if not isinstance(value,Mapping):raise AdditionalCostError('Additional costs must be objects')
    return FixedTapAdditionalCost.from_descriptor(value) if value.get('kind')==TAP_ADDITIONAL_COST_KIND else None


def fixed_tap_price_capabilities(cost: FixedTapAdditionalCost) -> tuple[str, ...]:
    if cost.schema_version == COLOR_TAP_SCHEMA_VERSION:
        return (TAP_ADDITIONAL_COST_CAPABILITY, COLOR_TAP_ADDITIONAL_COST_CAPABILITY)
    return (TAP_ADDITIONAL_COST_CAPABILITY,)


def fixed_tap_additional_cost_node_capabilities(*,cost_schema):
    if not isinstance(cost_schema,Mapping) or set(cost_schema)!={'additional_costs'}:return ()
    costs=cost_schema['additional_costs']
    if type(costs) is not list or len(costs)!=1:return ()
    try:cost=fixed_tap_additional_cost(costs[0])
    except (AdditionalCostError,TypeError):return ()
    return fixed_tap_price_capabilities(cost) if cost is not None else ()
