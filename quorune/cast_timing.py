from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence


CAST_PERMISSION_ACTIVE_ZONE = "playable"
CAST_PERMISSION_EVENT = "cast.permission"
PRINTED_FLASH_CAPABILITY = "timing.cast.printed_flash"
CLASSIC_FLASH_CAPABILITY = "casting.permission.classic_flash_cleanup"
CLASSIC_FLASH_HANDLER = "permission.cast.classic-flash-cleanup.v1"
CLASSIC_FLASH_MARK = "classic_flash_cleanup"
SURCHARGE_FLASH_HANDLER = "permission.cast.mana-surcharge-flash.v1"
SURCHARGE_FLASH_CAPABILITY = "timing.cast.mana_surcharge_flash"
SURCHARGE_FLASH_OPTION_FIELD = "mana_surcharge_flash"
PRINTED_FLASH_MECHANIC = PRINTED_FLASH_CAPABILITY.rsplit(".", 1)[1].removeprefix(
    "printed_"
)


class CastTimingPermissionError(ValueError):
    """A compiled cast-timing permission is malformed or unsupported."""


class OpponentUpkeepWindow(StrEnum):
    DURING = "during_upkeep"
    AFTER = "after_upkeep"


@dataclass(frozen=True, slots=True)
class OpponentUpkeepCastWindow:
    window: OpponentUpkeepWindow
    schema_version: int = 1

    def __post_init__(self):
        if (not isinstance(self.window, OpponentUpkeepWindow)
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise CastTimingPermissionError("Opponent upkeep casting requires an exact version-one window")

    def to_dict(self):
        return {"kind": "opponent_upkeep_window", "window": self.window.value, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"kind", "window", "schema_version"}
                or value["kind"] != "opponent_upkeep_window" or type(value["window"]) is not str):
            raise CastTimingPermissionError("Opponent upkeep cast window fields are closed")
        return cls(OpponentUpkeepWindow(value["window"]), value["schema_version"])

    def permits(self, state, actor: str) -> bool:
        active = state.active_player
        if (active == actor or active not in state.players or actor not in state.players
                or not state.players[active].in_game or not state.players[actor].in_game):
            return False
        if self.window is OpponentUpkeepWindow.DURING:
            return (state.phase, state.step) == ("beginning", "upkeep")
        return state.upkeep_completed is True


@dataclass(frozen=True, slots=True)
class ControllerCombatCastWindow:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CastTimingPermissionError("Own-combat casting requires the exact version-one window")

    def to_dict(self):
        return {"kind":"controller_combat_window", "schema_version":self.schema_version}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"kind","schema_version"}
                or value["kind"] != "controller_combat_window"):
            raise CastTimingPermissionError("Own-combat cast window fields are closed")
        return cls(value["schema_version"])

    def permits(self, state, actor: str) -> bool:
        player = state.players.get(actor)
        return (state.phase == "combat" and state.active_player == actor
                and player is not None and player.in_game)


@dataclass(frozen=True, slots=True)
class ManaSurchargeFlashSpec:
    """One fixed additional generic price for this face's timing permission."""

    generic: int
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CastTimingPermissionError('Unsupported mana-surcharge timing version')
        if type(self.generic) is not int or self.generic <= 0:
            raise CastTimingPermissionError('Instant-timing surcharge must be positive fixed generic mana')

    def to_dict(self):
        return {'schema_version':self.schema_version,'generic':self.generic}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','generic'}:
            raise CastTimingPermissionError('Mana-surcharge timing fields are closed')
        return cls(**value)


@dataclass(frozen=True, slots=True)
class CastTimingPermission:
    """One immutable permission that changes this face's casting timing."""

    timing: str = "instant"
    scope: str = "this_face"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CastTimingPermissionError(
                "Unsupported cast-timing permission schema version"
            )
        if self.timing != "instant":
            raise CastTimingPermissionError(
                "Only the closed instant-timing permission is represented"
            )
        if self.scope != "this_face":
            raise CastTimingPermissionError(
                "Cast-timing permission must be pinned to this card face"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "timing": self.timing,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "CastTimingPermission":
        expected = {"schema_version", "timing", "scope"}
        actual = set(value)
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        if missing:
            raise CastTimingPermissionError(
                "Cast-timing permission is missing required fields: "
                + ", ".join(missing)
            )
        if unknown:
            raise CastTimingPermissionError(
                "Cast-timing permission has unknown fields: "
                + ", ".join(unknown)
            )
        return cls(
            schema_version=value["schema_version"],
            timing=value["timing"],
            scope=value["scope"],
        )


class CastTimingState(Protocol):
    active_player: str
    phase: str
    stack: Sequence[Any]
    config: Any


def _card_types(type_line: str) -> frozenset[str]:
    card_types = type_line.split("—", 1)[0]
    return frozenset(card_types.casefold().split())


def type_line_has_card_type(type_line: str, card_type: str) -> bool:
    return card_type.casefold() in _card_types(type_line)


def cast_timing_is_legal(
    state: CastTimingState,
    seat: str,
    type_line: str,
    permissions: Sequence[CastTimingPermission] = (),
) -> bool:
    """Apply the shared ordinary spell-timing verdict used by offers and casts.

    Priority and zone permission are checked by their own owners. This boundary
    answers only whether the selected face may be cast in the current window.
    """

    card_types = _card_types(type_line)
    if "land" in card_types:
        return False
    if not bool(state.config.strict_timing):
        return True
    if "instant" in card_types or any(
        permission.timing == "instant" for permission in permissions
    ):
        return True
    return bool(
        seat == state.active_player
        and not state.stack
        and state.phase in {"precombat_main", "postcombat_main"}
    )


def canonical_cast_timing_permissions(
    values: Sequence[CastTimingPermission],
) -> tuple[CastTimingPermission, ...]:
    unique = {tuple(sorted(value.to_dict().items())): value for value in values}
    return tuple(unique[key] for key in sorted(unique))


__all__ = [
    "CAST_PERMISSION_ACTIVE_ZONE",
    "CAST_PERMISSION_EVENT",
    "CastTimingPermission",
    "CastTimingPermissionError",
    "canonical_cast_timing_permissions",
    "cast_timing_is_legal",
    "type_line_has_card_type",
]
