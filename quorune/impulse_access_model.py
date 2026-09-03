from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ImpulseAccessDuration(str, Enum):
    END_OF_TURN = "until_end_of_turn"
    END_OF_NEXT_TURN = "until_end_of_next_turn"
    UNTIL_USED = "until_used"


class TemporaryCastPermissionError(ValueError):
    """A typed temporary cast-permission grant is malformed."""


@dataclass(frozen=True, slots=True)
class TemporaryCastPermissionGrant:
    player: str
    duration: ImpulseAccessDuration
    not_before_turn_sequence: int | None
    without_mana_cost: bool
    source: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise TemporaryCastPermissionError(
                "Temporary cast-permission schema version is unsupported"
            )
        if type(self.player) is not str or not self.player:
            raise TemporaryCastPermissionError(
                "Temporary cast permission requires a player"
            )
        if not isinstance(self.duration, ImpulseAccessDuration) or self.duration not in {
            ImpulseAccessDuration.END_OF_TURN,
            ImpulseAccessDuration.UNTIL_USED,
        }:
            raise TemporaryCastPermissionError(
                "Temporary cast-permission duration is unsupported"
            )
        if type(self.without_mana_cost) is not bool:
            raise TemporaryCastPermissionError(
                "Temporary cast-permission payment policy must be boolean"
            )
        if type(self.source) is not str:
            raise TemporaryCastPermissionError(
                "Temporary cast-permission source must be a string"
            )
        if self.duration is ImpulseAccessDuration.UNTIL_USED:
            if (
                type(self.not_before_turn_sequence) is not int
                or self.not_before_turn_sequence < 0
            ):
                raise TemporaryCastPermissionError(
                    "Until-used cast permission requires a turn boundary"
                )
        elif self.not_before_turn_sequence is not None:
            raise TemporaryCastPermissionError(
                "Current-turn cast permission cannot carry a later boundary"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "player": self.player,
            "duration": self.duration.value,
            "not_before_turn_sequence": self.not_before_turn_sequence,
            "without_mana_cost": self.without_mana_cost,
            "source": self.source,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "TemporaryCastPermissionGrant":
        expected = {
            "schema_version",
            "player",
            "duration",
            "not_before_turn_sequence",
            "without_mana_cost",
            "source",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise TemporaryCastPermissionError(
                "Temporary cast-permission grants have a closed schema"
            )
        try:
            duration = ImpulseAccessDuration(value["duration"])
        except (TypeError, ValueError) as exc:
            raise TemporaryCastPermissionError(
                "Temporary cast-permission duration is unsupported"
            ) from exc
        return cls(
            player=value["player"],
            duration=duration,
            not_before_turn_sequence=value["not_before_turn_sequence"],
            without_mana_cost=value["without_mana_cost"],
            source=value["source"],
            schema_version=value["schema_version"],
        )


__all__ = [
    "ImpulseAccessDuration",
    "TemporaryCastPermissionError",
    "TemporaryCastPermissionGrant",
]
