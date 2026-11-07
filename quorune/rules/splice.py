from __future__ import annotations

"""Closed hand-source declarations for adding one spell body while casting."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..replacement.immutable import FrozenMap, thaw_value
from .casting_additional_costs import FixedZoneChangeAdditionalCost


SPLICE_CAPABILITY = "casting.additional_cost.splice"
SPLICE_HANDLER = "casting.cost.splice.v1"
SPLICE_SELECTION = "splice_sources"
SPLICE_INTERNAL_SELECTION = "_splice_sources"
SPLICE_REQUEST = "prepare_splice"
_SYMBOLS = frozenset(("GENERIC", *"WUBRGC"))


@dataclass(frozen=True, slots=True)
class SpliceSpec:
    quality: str
    mana: Mapping[str, int] | None = None
    zone_change: FixedZoneChangeAdditionalCost | None = None

    def __post_init__(self) -> None:
        if self.quality not in {"arcane", "instant_or_sorcery"}:
            raise ValueError("Splice requires a represented host quality")
        if (self.mana is None) == (self.zone_change is None):
            raise ValueError("Splice requires exactly one fixed price")
        if self.mana is not None:
            if (not isinstance(self.mana, Mapping) or set(self.mana) - _SYMBOLS or any(
                type(amount) is not int or amount < 0 for amount in self.mana.values()
            )):
                raise ValueError("Splice requires ordinary fixed mana")
            object.__setattr__(self, "mana", FrozenMap(self.mana))
        elif (not isinstance(self.zone_change, FixedZoneChangeAdditionalCost)
            or self.zone_change.count_parameter is not None):
            raise ValueError("Splice requires a fixed typed zone price")

    def matches(self, types, subtypes) -> bool:
        if self.quality == "arcane":
            return "arcane" in subtypes
        return bool({"instant", "sorcery"}.intersection(types))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "quality": self.quality,
            "mana": thaw_value(self.mana),
            "zone_change": self.zone_change.to_descriptor() if self.zone_change is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SpliceSpec:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "quality", "mana", "zone_change"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["quality"]) is not str):
            raise ValueError("Splice declaration fields are closed")
        zone_change = value["zone_change"]
        return cls(value["quality"], value["mana"],
            FixedZoneChangeAdditionalCost.from_descriptor(zone_change) if zone_change is not None else None)
