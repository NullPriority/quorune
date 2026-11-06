from __future__ import annotations

"""Typed instructions and public incarnation references for CR 701.66."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


EARTHBEND_OPERATION = "earthbend_land"
EARTHBEND_FINISH_OPERATION = "finish_earthbend_land"
EARTHBEND_RETURN_OPERATION = "return_earthbent_objects"
EARTHBEND_CAPABILITY = "keyword_action.earthbend"
EARTHBEND_MECHANIC = "earthbend-land"
EARTHBEND_EVENT = "player.earthbends"
EARTHBEND_DEPARTURE = "earthbend.land_departure"
EARTHBEND_RETURN_ZONES = frozenset({"graveyard", "exile"})


@dataclass(frozen=True, slots=True)
class EarthbendObject:
    object_id: str
    logical_object_id: str
    reference: str
    zone: str
    zone_change_counter: int

    def __post_init__(self) -> None:
        identities = (self.object_id, self.logical_object_id, self.reference)
        if any(type(value) is not str or not value for value in identities):
            raise ValueError("Earthbend objects require stable identities")
        if type(self.zone) is not str or self.zone not in EARTHBEND_RETURN_ZONES | {"battlefield"}:
            raise ValueError("Earthbend objects require a represented public zone")
        if type(self.zone_change_counter) is not int or self.zone_change_counter < 0:
            raise ValueError("Earthbend objects require a zone-change counter")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EarthbendObject:
        if not isinstance(value, Mapping) or set(value) != {
            "object_id", "logical_object_id", "reference", "zone", "zone_change_counter",
        }:
            raise ValueError("Earthbend object has an invalid shape")
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "reference": self.reference,
            "zone": self.zone,
            "zone_change_counter": self.zone_change_counter,
        }


def earthbend_resolution_values(value: Mapping[str, Any]) -> tuple[str, int]:
    if set(value) != {"op", "target", "amount"} or value["op"] != EARTHBEND_OPERATION:
        raise ValueError("Earthbend requires a target land and a resolved amount")
    target, amount = value["target"], value["amount"]
    if type(target) is not str or not target or type(amount) is not int or amount < 0:
        raise ValueError("Earthbend has an invalid target or amount")
    return target, amount
