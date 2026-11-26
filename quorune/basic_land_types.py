from __future__ import annotations

"""Shared land subtype vocabulary and the all-nonbasic definition."""

from collections.abc import Mapping
from dataclasses import dataclass

BASIC_LAND_MANA=dict(zip('plains island swamp mountain forest'.split(),'WUBRG',strict=True))
LAND_SUBTYPES=frozenset({
    'cave','desert','forest','gate','island','lair','locus','mine','mountain',
    'plains','planet','power-plant','sphere','swamp','tower','town',"urza's",
})


NONBASIC_LAND_SUBTYPES = LAND_SUBTYPES.difference(BASIC_LAND_MANA)
ALL_NONBASIC_LAND_TYPES_CAPABILITY = "continuous.characteristics.all_nonbasic_land_types"
ALL_NONBASIC_LAND_TYPES_HANDLER = "ability.static.all-nonbasic-land-types-definition.v1"


@dataclass(frozen=True, slots=True)
class AllNonbasicLandTypesDefinitionSpec:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("The all-nonbasic-land-types definition requires schema version one")

    def to_dict(self):
        return {"schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version"}:
            raise ValueError("The all-nonbasic-land-types definition has a closed schema")
        return cls(value["schema_version"])
