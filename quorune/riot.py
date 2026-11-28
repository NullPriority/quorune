from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


RIOT_MECHANIC = "riot"
RIOT_COUNTER = "+1/+1"
RIOT_ENTRY_HANDLER_ID = "replacement.zone.riot-entry-choice.v1"

RIOT_GRANT_CAPABILITY = "continuous.ability.nontoken_riot_grant"
RIOT_GRANT_HANDLER_ID = "continuous.keyword.nontoken-riot-grant.v1"
RIOT_GRANTED_FRAGMENT = "granted_riot"
RIOT_GRANTED_COMPONENT_PREFIX = "granted-riot:"


@dataclass(frozen=True, slots=True)
class GrantedRiotSpec:
    """One separately tracked layer-six grant from a current provider incarnation."""

    instance_id: str

    def __post_init__(self) -> None:
        if type(self.instance_id) is not str or not self.instance_id or self.instance_id.strip() != self.instance_id:
            raise ValueError("Granted Riot requires its exact current instance identity")

    def to_dict(self):
        return {"schema_version": 1, "instance_id": self.instance_id}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "instance_id"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Granted Riot fragment fields are closed")
        return cls(value["instance_id"])


def riot_grant_handler_descriptor():
    return {
        "handler_id": RIOT_GRANT_HANDLER_ID,
        "schema_version": 1,
        "event": "characteristics.evaluate",
    }



def riot_entry_handler_descriptor() -> dict[str, object]:
    return {
        "handler_id": RIOT_ENTRY_HANDLER_ID,
        "schema_version": 1,
        "event": "zone.change",
        "counter_name": RIOT_COUNTER,
        "amount": 1,
        "alternative_keyword": "haste",
        "rule_id": "702.136a",
    }


__all__ = [
    "RIOT_COUNTER",
    "RIOT_ENTRY_HANDLER_ID",
    "RIOT_MECHANIC",
    "riot_entry_handler_descriptor",
]
