from __future__ import annotations

"""Typed game-duration hand-size rules created by a resolving stack item."""

from dataclasses import dataclass
from typing import Any, ClassVar, Mapping

from .continuous_effect_model import ContinuousEffectDuration

OPERATION = "grant_controller_no_maximum_hand_size"
CAPABILITY = "hand.cleanup.no_maximum_for_game"
EFFECT_KIND = "resolved_no_maximum_hand_size"
_DURATION = ContinuousEffectDuration.UNTIL_END_OF_GAME


def no_maximum_for_game_instruction(player: str = "$controller") -> dict[str, Any]:
    return {"op": OPERATION, "schema_version": 1, "player": player}


def require_no_maximum_for_game_instruction(effect: Mapping[str, Any], *, player: str) -> None:
    if type(effect.get("schema_version")) is not int or effect != no_maximum_for_game_instruction(player):
        raise ValueError("A game-duration hand-size grant requires the complete resolving-player instruction")


@dataclass(frozen=True, slots=True)
class NoMaximumForGameIntent:
    controller: str
    stack_ref: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.controller, self.stack_ref)):
            raise ValueError("A game-duration hand-size grant requires a controller and stack identity")


@dataclass(frozen=True, slots=True)
class ResolvedNoMaximumHandSize:
    effect_id: str
    source_id: str
    timestamp: int
    controller: str
    duration: ClassVar[ContinuousEffectDuration] = _DURATION

    def __post_init__(self) -> None:
        if (any(type(value) is not str or not value for value in
                (self.effect_id, self.source_id, self.controller))
                or type(self.timestamp) is not int or self.timestamp < 0):
            raise ValueError("A game-duration hand-size rule requires a fixed player, identity and timestamp")

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_kind": EFFECT_KIND,
            "schema_version": 1,
            "effect_id": self.effect_id,
            "source_id": self.source_id,
            "timestamp": self.timestamp,
            "controller": self.controller,
            "duration": self.duration.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResolvedNoMaximumHandSize:
        if (not isinstance(value, Mapping)
                or set(value) != {"effect_kind", "schema_version", "effect_id", "source_id",
                           "timestamp", "controller", "duration"}
                or value["effect_kind"] != EFFECT_KIND
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["duration"] != _DURATION.value):
            raise ValueError("Malformed game-duration hand-size rule")
        return cls(value["effect_id"], value["source_id"], value["timestamp"], value["controller"])
