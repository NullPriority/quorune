from __future__ import annotations

"""One closed, typed hand reveal leaf in the existing casting-cost algebra."""

from dataclasses import dataclass
from collections.abc import Mapping
from ..creature_subtypes import canonical_creature_subtype
from .casting_additional_costs import AdditionalCostError

REVEAL_ADDITIONAL_COST_KIND='reveal_from_hand'
REVEAL_ADDITIONAL_COST_CAPABILITY='casting.additional_cost.fixed_reveal'


@dataclass(frozen=True,slots=True)
class FixedRevealAdditionalCost:
    subtype: str
    schema_version: int=1
    kind: str=REVEAL_ADDITIONAL_COST_KIND

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1 or self.kind!=REVEAL_ADDITIONAL_COST_KIND
                or type(self.subtype) is not str or canonical_creature_subtype(self.subtype)!=self.subtype):
            raise AdditionalCostError('Reveal additional cost requires one canonical creature subtype')

    def to_descriptor(self):return {'schema_version':1,'kind':self.kind,'subtype':self.subtype}

    @classmethod
    def from_descriptor(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','kind','subtype'}:
            raise AdditionalCostError('Reveal additional-cost descriptor fields are closed')
        return cls(**value)


def fixed_reveal_additional_cost(value):
    if not isinstance(value,Mapping):raise AdditionalCostError('Additional costs must be objects')
    return FixedRevealAdditionalCost.from_descriptor(value) if value.get('kind')==REVEAL_ADDITIONAL_COST_KIND else None
