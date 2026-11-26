from __future__ import annotations

"""One closed, typed hand reveal leaf in the existing casting-cost algebra."""

from dataclasses import dataclass
from collections.abc import Mapping
from ..creature_subtypes import canonical_creature_subtype
from .casting_additional_costs import AdditionalCostError

REVEAL_ADDITIONAL_COST_KIND='reveal_from_hand'
REVEAL_ADDITIONAL_COST_CAPABILITY='casting.additional_cost.fixed_reveal'
CREATURE_TYPE_REVEAL_VERSION = 2


@dataclass(frozen=True,slots=True)
class FixedRevealAdditionalCost:
    subtype: str | None
    schema_version: int=1
    kind: str=REVEAL_ADDITIONAL_COST_KIND

    def __post_init__(self):
        if type(self.schema_version) is not int or self.kind != REVEAL_ADDITIONAL_COST_KIND:
            raise AdditionalCostError("Reveal additional cost requires a registered schema")
        if self.schema_version == CREATURE_TYPE_REVEAL_VERSION and self.subtype is None:
            return
        if (self.schema_version != 1 or type(self.subtype) is not str
                or canonical_creature_subtype(self.subtype) != self.subtype):
            raise AdditionalCostError('Reveal additional cost requires one canonical creature subtype')

    @property
    def reveals_creature_type(self):
        return self.schema_version == CREATURE_TYPE_REVEAL_VERSION

    def to_descriptor(self):
        base = {"schema_version": self.schema_version, "kind": self.kind}
        return {**base, "card_type": "creature"} if self.reveals_creature_type else {**base, "subtype": self.subtype}

    @classmethod
    def from_descriptor(cls,value):
        if not isinstance(value, Mapping):
            raise AdditionalCostError('Reveal additional-cost descriptor fields are closed')
        if set(value) == {"schema_version", "kind", "card_type"} and value["card_type"] == "creature":
            if type(value["schema_version"]) is int and value["schema_version"] == CREATURE_TYPE_REVEAL_VERSION:
                return cls(subtype=None, schema_version=value["schema_version"], kind=value["kind"])
        if set(value) == {"schema_version", "kind", "subtype"} and type(value["schema_version"]) is int and value["schema_version"] == 1:
            return cls(**value)
        raise AdditionalCostError('Reveal additional-cost descriptor fields are closed')


def fixed_reveal_additional_cost(value):
    if not isinstance(value,Mapping):raise AdditionalCostError('Additional costs must be objects')
    return FixedRevealAdditionalCost.from_descriptor(value) if value.get('kind')==REVEAL_ADDITIONAL_COST_KIND else None
