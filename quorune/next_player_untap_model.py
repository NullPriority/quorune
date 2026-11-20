from __future__ import annotations

"""Closed pending prohibitions for one player's next actual untap step."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


NEXT_PLAYER_UNTAP_OPERATION = "prohibit_next_untap"
NEXT_PLAYER_UNTAP_INSTRUCTION_VERSION = 2


class NextPlayerUntapScope(StrEnum):
    LANDS = "lands"
    CREATURES_AND_LANDS = "creatures_and_lands"

    @property
    def permanent_types(self) -> frozenset[str]:
        return (frozenset({"land"}) if self is NextPlayerUntapScope.LANDS
            else frozenset({"creature", "land"}))

    @property
    def player_relation(self) -> str:
        return "any" if self is NextPlayerUntapScope.LANDS else "opponent"


@dataclass(frozen=True, slots=True)
class NextPlayerUntapRestriction:
    player: str
    scope: NextPlayerUntapScope

    def __post_init__(self) -> None:
        if type(self.player) is not str or not self.player or not isinstance(self.scope, NextPlayerUntapScope):
            raise ValueError("Next-player untap restrictions require a player and typed permanent scope")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "player": self.player, "scope": self.scope.value}

    @classmethod
    def from_dict(cls, value: Any) -> NextPlayerUntapRestriction:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "player", "scope"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["scope"]) is not str):
            raise ValueError("Next-player untap restriction uses a closed versioned schema")
        return cls(value["player"], NextPlayerUntapScope(value["scope"]))


@dataclass(frozen=True, slots=True)
class ProhibitNextPlayerUntapIntent:
    actor: str
    restriction: NextPlayerUntapRestriction
    reason: str

    def __post_init__(self) -> None:
        if (type(self.actor) is not str or not self.actor or type(self.reason) is not str or not self.reason
                or not isinstance(self.restriction, NextPlayerUntapRestriction)):
            raise ValueError("Next-player untap instructions require an actor, reason and typed restriction")
        if self.actor == self.restriction.player and self.restriction.scope.player_relation == "opponent":
            raise ValueError("This next-player untap scope requires an opponent")


def decode_next_player_untap_restrictions(value: Any, players: Mapping[str, Any]) -> tuple[NextPlayerUntapRestriction, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Next-player untap restrictions must be an array")
    restrictions = tuple(NextPlayerUntapRestriction.from_dict(row) for row in value)
    seats = [restriction.player for restriction in restrictions]
    if seats != sorted(set(seats)) or any(seat not in players for seat in seats):
        raise ValueError("Next-player untap restrictions require sorted unique known seats")
    return restrictions


def is_next_player_untap_instruction(value: Mapping[str, Any]) -> bool:
    return (value.get("op") == NEXT_PLAYER_UNTAP_OPERATION
        and any(field in value for field in ("schema_version", "player", "scope")))


def decode_next_player_untap_instruction(value: Mapping[str, Any]) -> NextPlayerUntapRestriction:
    if (set(value) != {"op", "schema_version", "player", "scope"}
            or value["op"] != NEXT_PLAYER_UNTAP_OPERATION
            or type(value["schema_version"]) is not int
            or value["schema_version"] != NEXT_PLAYER_UNTAP_INSTRUCTION_VERSION
            or type(value["scope"]) is not str):
        raise ValueError("Next-player untap instruction uses a closed versioned schema")
    return NextPlayerUntapRestriction(value["player"], NextPlayerUntapScope(value["scope"]))


def next_player_untap_target_schema(scope: NextPlayerUntapScope) -> dict[str, Any]:
    return {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": scope.player_relation}
