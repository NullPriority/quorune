from __future__ import annotations

"""CR701.4: one fixed quality, with distinct nonmoving hand/public selections."""

from dataclasses import dataclass
from collections.abc import Mapping
from ..creature_subtypes import canonical_creature_subtype
from .casting_additional_costs import AdditionalCostError

BEHOLD_ADDITIONAL_COST_KIND = 'behold'
BEHOLD_ADDITIONAL_COST_CAPABILITY = 'casting.additional_cost.fixed_behold'
BEHOLD_COST_INFORMATION_FIELD = 'behold_casting_cost_information'


@dataclass(frozen=True, slots=True)
class FixedBeholdAdditionalCost:
    subtype: str
    schema_version: int = 1
    kind: str = BEHOLD_ADDITIONAL_COST_KIND

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or self.kind != BEHOLD_ADDITIONAL_COST_KIND or type(self.subtype) is not str
                or canonical_creature_subtype(self.subtype) != self.subtype):
            raise AdditionalCostError('Behold additional cost requires one canonical creature subtype')

    def to_descriptor(self):
        return {'schema_version': 1, 'kind': self.kind, 'subtype': self.subtype}

    @classmethod
    def from_descriptor(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind', 'subtype'}:
            raise AdditionalCostError('Behold additional-cost descriptor fields are closed')
        return cls(**value)


def fixed_behold_additional_cost(value):
    if not isinstance(value, Mapping):
        raise AdditionalCostError('Additional costs must be objects')
    return FixedBeholdAdditionalCost.from_descriptor(value) if value.get('kind') == BEHOLD_ADDITIONAL_COST_KIND else None


def behold_cost_dependencies():
    from .reveal_additional_cost import REVEAL_ADDITIONAL_COST_CAPABILITY
    return (BEHOLD_ADDITIONAL_COST_CAPABILITY, REVEAL_ADDITIONAL_COST_CAPABILITY)
