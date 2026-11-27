from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .combat_damage_library_exile import (
    COMBAT_DAMAGE_CAST_EXILE_VERSION, COMBAT_DAMAGE_EXILE_COUNT, COMBAT_DAMAGE_EXILE_VERSION,
)
from .defending_player_library_exile import DEFENDING_PLAYER_EXILE_COUNT, DEFENDING_PLAYER_EXILE_VERSION
from .fractional_quantity_model import HalfQuantitySpec
from .opponent_library_top_model import OPPONENT_HALF_TOP_EXILE_VERSION, OPPONENT_TOP_EXILE_VERSION
from .land_entry_library_exile_model import LAND_ENTRY_TOP_EXILE_COUNT, LAND_ENTRY_TOP_EXILE_VERSION


IMPULSE_ACCESS_DECLARED_COUNT_VERSION = 2
SHUFFLED_TOP_FREE_PLAY_VERSION = 9
TOP_FREE_INSTANT_SORCERY_VERSION = 10
IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY = "zone.impulse_access.declared_count"
IMPULSE_ACCESS_NEXT_END_STEP_CAPABILITY = "zone.impulse_access.next_end_step"


def impulse_access_count_is_legal(count, version) -> bool:
    if type(count) is not int or type(version) is not int:
        return False
    if version in {IMPULSE_ACCESS_DECLARED_COUNT_VERSION, OPPONENT_HALF_TOP_EXILE_VERSION}:
        return count >= 0
    if version == OPPONENT_TOP_EXILE_VERSION:
        return count == 1
    if version == DEFENDING_PLAYER_EXILE_VERSION:
        return count == DEFENDING_PLAYER_EXILE_COUNT
    if version == LAND_ENTRY_TOP_EXILE_VERSION:
        return count == LAND_ENTRY_TOP_EXILE_COUNT
    if version == COMBAT_DAMAGE_EXILE_VERSION:
        return count == COMBAT_DAMAGE_EXILE_COUNT
    if version in {COMBAT_DAMAGE_CAST_EXILE_VERSION, SHUFFLED_TOP_FREE_PLAY_VERSION}:
        return count == 1
    if version == TOP_FREE_INSTANT_SORCERY_VERSION:
        return count == 8
    return version == 1 and 1 <= count <= 10


def impulse_access_request_count_is_legal(count, version) -> bool:
    if type(version) is int and version == OPPONENT_HALF_TOP_EXILE_VERSION:
        return isinstance(count, HalfQuantitySpec) and count == HalfQuantitySpec("up")
    return impulse_access_count_is_legal(count, version)


class ImpulseAccessDuration(str, Enum):
    END_OF_TURN = "until_end_of_turn"
    END_OF_NEXT_TURN = "until_end_of_next_turn"
    NEXT_END_STEP = "until_next_end_step"
    UNTIL_USED = "until_used"


def shuffled_top_free_play_effect() -> dict[str, Any]:
    return {
        "op": "fixed_impulse_access", "schema_version": SHUFFLED_TOP_FREE_PLAY_VERSION,
        "player": "$controller", "count": 1, "duration": ImpulseAccessDuration.END_OF_TURN.value,
    }


def validate_shuffled_top_free_play(effect: Mapping[str, Any], *, actor: str | None = None) -> None:
    expected = shuffled_top_free_play_effect()
    if actor is not None:
        expected["player"] = actor
    if (
        not isinstance(effect, Mapping) or dict(effect) != expected
        or type(effect["schema_version"]) is not int or type(effect["count"]) is not int
    ):
        raise ValueError("Shuffled top free play requires one controller card until end of turn")


def top_free_instant_sorcery_effect():
    return {
        "op": "fixed_impulse_access", "schema_version": TOP_FREE_INSTANT_SORCERY_VERSION,
        "player": "$controller", "count": 8, "duration": ImpulseAccessDuration.END_OF_TURN.value,
    }


def validate_top_free_instant_sorcery(effect: Mapping[str, Any], *, actor: str | None = None) -> None:
    expected = top_free_instant_sorcery_effect()
    if actor is not None:
        expected["player"] = actor
    if (not isinstance(effect, Mapping) or dict(effect) != expected
            or type(effect["schema_version"]) is not int or type(effect["count"]) is not int):
        raise ValueError("Free instant or sorcery access requires exactly the controller's top eight until end of turn")


def impulse_access_play_policy_is_legal(actor, supplier, duration, version):
    if version in {SHUFFLED_TOP_FREE_PLAY_VERSION, TOP_FREE_INSTANT_SORCERY_VERSION}:
        return actor == supplier and duration is ImpulseAccessDuration.END_OF_TURN
    if version == OPPONENT_TOP_EXILE_VERSION:
        return actor != supplier and (duration is None or duration is ImpulseAccessDuration.END_OF_TURN)
    if version in {DEFENDING_PLAYER_EXILE_VERSION, OPPONENT_HALF_TOP_EXILE_VERSION, LAND_ENTRY_TOP_EXILE_VERSION}:
        return actor != supplier and duration is None
    if version in {COMBAT_DAMAGE_EXILE_VERSION, COMBAT_DAMAGE_CAST_EXILE_VERSION}:
        return actor != supplier and duration is ImpulseAccessDuration.END_OF_TURN
    return isinstance(duration, ImpulseAccessDuration)


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
