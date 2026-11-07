from __future__ import annotations

"""Closed instructions and recorded grants for CR 614.10."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


TURN_SKIP_OPERATION = "schedule_turn_skip"
TURN_SKIP_MECHANIC = "fixed-scheduled-turn-skip"
TURN_SKIP_CAPABILITY = "turn.boundary.scheduled_skip"
TURN_BOUNDARY_SKIP_RESUME = "turn_boundary_skip"
TURN_BOUNDARY_SKIP_EVENT = "turn.boundary.skip"
MAX_PRINTED_SKIP_COUNT = 10


class TurnBoundary(StrEnum):
    TURN = "turn"
    COMBAT = "combat"
    UNTAP = "untap"
    UPKEEP = "upkeep"
    DRAW = "draw"


class TurnSkipScope(StrEnum):
    NEXT = "next"
    THIS_TURN = "this_turn"
    ALL_OF_NEXT_TURN = "all_of_next_turn"


@dataclass(frozen=True, slots=True)
class TurnSkipSpec:
    boundary: TurnBoundary
    scope: TurnSkipScope = TurnSkipScope.NEXT
    count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.boundary, TurnBoundary) or not isinstance(self.scope, TurnSkipScope):
            raise ValueError("Scheduled skip requires a typed boundary and scope")
        if type(self.count) is not int or self.count < 1:
            raise ValueError("Scheduled skip count must be a positive integer")
        if self.boundary is not TurnBoundary.TURN and self.count != 1:
            raise ValueError("Only whole-turn skips represent multiple occurrences")
        if self.scope is not TurnSkipScope.NEXT and self.boundary is not TurnBoundary.COMBAT:
            raise ValueError("Turn-scoped skips require combat phases")

    def to_dict(self) -> dict[str, Any]:
        return {"boundary": self.boundary.value, "scope": self.scope.value, "count": self.count}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TurnSkipSpec:
        if not isinstance(value, Mapping) or set(value) != {"boundary", "scope", "count"}:
            raise ValueError("Scheduled skip instruction has a closed schema")
        return cls(TurnBoundary(value["boundary"]), TurnSkipScope(value["scope"]), value["count"])


@dataclass(frozen=True, slots=True)
class ScheduledTurnSkip:
    grant_id: str
    player: str
    spec: TurnSkipSpec
    turn_sequence: int | None = None

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.grant_id, self.player)):
            raise ValueError("Scheduled skip grant requires an identity and player")
        if not isinstance(self.spec, TurnSkipSpec):
            raise ValueError("Scheduled skip grant requires a typed instruction")
        sequence = self.turn_sequence
        if sequence is not None and (type(sequence) is not int or sequence < 0):
            raise ValueError("Scheduled skip grant has an invalid turn binding")
        if (
            self.spec.scope is TurnSkipScope.NEXT and sequence is not None
            or self.spec.scope is TurnSkipScope.THIS_TURN and sequence is None
        ):
            raise ValueError("Scheduled skip grant has an incompatible turn binding")

    def applies(self, player: str, boundary: TurnBoundary, turn_sequence: int) -> bool:
        return (
            self.player == player and self.spec.boundary is boundary
            and (self.spec.scope is TurnSkipScope.NEXT or self.turn_sequence == turn_sequence)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id, "player": self.player, "spec": self.spec.to_dict(),
            "turn_sequence": self.turn_sequence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ScheduledTurnSkip:
        if not isinstance(value, Mapping) or set(value) != {"grant_id", "player", "spec", "turn_sequence"}:
            raise ValueError("Scheduled skip grant has a closed schema")
        return cls(value["grant_id"], value["player"], TurnSkipSpec.from_dict(value["spec"]), value["turn_sequence"])


@dataclass(frozen=True, slots=True)
class ScheduleTurnSkipIntent:
    actor: str
    players: tuple[str, ...]
    spec: TurnSkipSpec

    def __post_init__(self) -> None:
        if type(self.actor) is not str or not self.actor or not isinstance(self.spec, TurnSkipSpec):
            raise ValueError("Scheduled skip requires a resolving controller and typed instruction")
        if not isinstance(self.players, tuple) or any(type(seat) is not str or not seat for seat in self.players):
            raise ValueError("Scheduled skip requires an ordered seat set")
        if len(self.players) != len(set(self.players)):
            raise ValueError("Scheduled skip recipients must be distinct")


def decode_scheduled_turn_skips(
    value: Any, players: Mapping[str, Any], *, active_player: str | None, turn_sequence: int
) -> list[ScheduledTurnSkip]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Scheduled skip grants must be an array")
    grants = [ScheduledTurnSkip.from_dict(item) for item in value]
    if len(grants) != len({grant.grant_id for grant in grants}):
        raise ValueError("Scheduled skip grant identities must be unique")
    if any(grant.player not in players for grant in grants):
        raise ValueError("Scheduled skip grant names an unknown seat")
    if any(
        grant.turn_sequence is not None and (grant.turn_sequence != turn_sequence or grant.player != active_player)
        for grant in grants
    ):
        raise ValueError("Scheduled skip grant names a stale turn")
    if len([grant for grant in grants if grant.spec.boundary is TurnBoundary.TURN]) != len({
        grant.player for grant in grants if grant.spec.boundary is TurnBoundary.TURN
    }):
        raise ValueError("Whole-turn skip occurrences must be combined for each seat")
    return grants


def decode_turn_skip_instruction(value: Mapping[str, Any]) -> TurnSkipSpec:
    keys = set(value)
    if (
        value.get("op") != TURN_SKIP_OPERATION
        or keys not in ({"op", "spec", "player"}, {"op", "spec", "players"})
        or ("players" in value and value["players"] != "opponents")
        or ("player" in value and (type(value["player"]) is not str or not value["player"]))
    ):
        raise ValueError("Scheduled skip requires one closed recipient")
    spec = TurnSkipSpec.from_dict(value["spec"])
    if spec.count > MAX_PRINTED_SKIP_COUNT:
        raise ValueError("Scheduled skip exceeds the fixed instruction count limit")
    return spec
