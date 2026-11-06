"""Immutable dungeon transitions and continuation data under CR 309 and 701.49."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .dungeon_catalog import DUNGEON_CATALOG, DungeonId, VentureMode


def _require_fields(value: Any, fields: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} has an invalid shape")


@dataclass(frozen=True, slots=True)
class DungeonRoomRef:
    dungeon: DungeonId
    index: int

    def __post_init__(self) -> None:
        if not isinstance(self.dungeon, DungeonId) or type(self.index) is not int or not (
            0 <= self.index < len(DUNGEON_CATALOG[self.dungeon].rooms)
        ):
            raise ValueError("Dungeon room must name an existing room in the traditional pool")

    @property
    def exits(self) -> tuple[int, ...]:
        return DUNGEON_CATALOG[self.dungeon].rooms[self.index].exits

    def to_dict(self) -> dict[str, str | int]:
        return {"dungeon": self.dungeon.value, "index": self.index}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DungeonRoomRef:
        _require_fields(value, {"dungeon", "index"}, "Dungeon room")
        return cls(DungeonId(value["dungeon"]), value["index"])


@dataclass(frozen=True, slots=True)
class DungeonVisit:
    room: DungeonRoomRef
    incarnation: int

    def __post_init__(self) -> None:
        if not isinstance(self.room, DungeonRoomRef) or type(self.incarnation) is not int or self.incarnation < 1:
            raise ValueError("Dungeon visit requires its room and positive incarnation")

    def to_dict(self) -> dict[str, Any]:
        return {"room": self.room.to_dict(), "incarnation": self.incarnation}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DungeonVisit:
        _require_fields(value, {"room", "incarnation"}, "Dungeon visit")
        return cls(DungeonRoomRef.from_dict(value["room"]), value["incarnation"])


@dataclass(frozen=True, slots=True)
class DungeonJourney:
    """One player's current command-zone dungeon and completed count."""

    active: DungeonVisit | None = None
    completed: int = 0

    def __post_init__(self) -> None:
        if type(self.completed) is not int or self.completed < 0:
            raise ValueError("Dungeon completion count must be a nonnegative integer")
        if self.active is not None and (
            not isinstance(self.active, DungeonVisit) or self.active.incarnation != self.completed + 1
        ):
            raise ValueError("The active dungeon must be the owner's next incarnation")

    def to_dict(self) -> dict[str, Any]:
        return {"active": self.active.to_dict() if self.active is not None else None, "completed": self.completed}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DungeonJourney:
        _require_fields(value, {"active", "completed"}, "Dungeon journey")
        active = DungeonVisit.from_dict(value["active"]) if value["active"] is not None else None
        return cls(active, value["completed"])


@dataclass(frozen=True, slots=True)
class VentureOffer:
    """The current player's exact room or dungeon selection boundary."""

    owner: str
    mode: VentureMode
    journey: DungeonJourney

    def __post_init__(self) -> None:
        if type(self.owner) is not str or not self.owner or not isinstance(self.mode, VentureMode):
            raise ValueError("Venture requires its owner and entry mode")
        if not isinstance(self.journey, DungeonJourney) or (
            self.journey.active is not None and not self.journey.active.room.exits
        ):
            raise ValueError("A venture offer requires a journey after any last-room completion")

    @property
    def destinations(self) -> tuple[DungeonRoomRef, ...]:
        if self.journey.active is not None:
            room = self.journey.active.room
            return tuple(DungeonRoomRef(room.dungeon, index) for index in room.exits)
        return tuple(
            DungeonRoomRef(dungeon, 0)
            for dungeon, definition in DUNGEON_CATALOG.items()
            if definition.entry_mode is self.mode
        )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "owner": self.owner, "mode": self.mode.value,
                "journey": self.journey.to_dict(), "destinations": [room.to_dict() for room in self.destinations]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VentureOffer:
        _require_fields(value, {"schema_version", "owner", "mode", "journey", "destinations"}, "Venture offer")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("Venture offer requires version 1")
        if not isinstance(value["destinations"], (list, tuple)):
            raise ValueError("Venture offer requires an ordered destination list")
        offer = cls(value["owner"], VentureMode(value["mode"]), DungeonJourney.from_dict(value["journey"]))
        if tuple(DungeonRoomRef.from_dict(room) for room in value["destinations"]) != offer.destinations:
            raise ValueError("Venture destinations do not match the pinned graph and current room")
        return offer


def complete_finished_dungeon(
    journey: DungeonJourney, outstanding_incarnations: Iterable[int],
) -> tuple[DungeonJourney, DungeonVisit | None]:
    """Apply CR 309.6 using the owner's pending and stacked room abilities."""
    outstanding = tuple(outstanding_incarnations)
    if any(type(incarnation) is not int or incarnation < 1 for incarnation in outstanding):
        raise ValueError("Outstanding room abilities require positive dungeon incarnations")
    active = journey.active
    if active is None or active.room.exits or active.incarnation in outstanding:
        return journey, None
    return DungeonJourney(completed=journey.completed + 1), active


def prepare_venture(owner: str, journey: DungeonJourney, mode: VentureMode) -> tuple[VentureOffer, DungeonVisit | None]:
    """Retire a bottommost dungeon before offering the next entry under CR 701.49c."""
    # This instruction retires the old dungeon even while its room abilities remain.
    after_retirement, completed = complete_finished_dungeon(journey, ())
    return VentureOffer(owner, mode, after_retirement), completed


def accept_venture(owner: str, journey: DungeonJourney, offer: VentureOffer, selected: DungeonRoomRef) -> DungeonJourney:
    """Validate the retained offer against its owner and live journey before entering."""
    if not isinstance(offer, VentureOffer) or owner != offer.owner or journey != offer.journey:
        raise ValueError("Venture offer no longer belongs to this owner and dungeon position")
    if not isinstance(selected, DungeonRoomRef) or selected not in offer.destinations:
        raise ValueError("The selected dungeon room was not offered")
    return DungeonJourney(DungeonVisit(selected, journey.completed + 1), journey.completed)
