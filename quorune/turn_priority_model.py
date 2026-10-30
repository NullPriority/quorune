from __future__ import annotations

"""Immutable transition values for the turn/priority decision state machine."""

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence

from .model import GameState


def apnap_participants(
    turn_order: Sequence[str], participants: Sequence[str], turn_anchor: str | None,
) -> tuple[str, ...]:
    """Rotate physical seats before omitting departed decision participants."""
    seats = tuple(turn_order)
    live = tuple(participants)
    if (not seats or any(type(seat) is not str or not seat for seat in seats)
            or len(seats) != len(set(seats))
            or any(type(seat) is not str or seat not in seats for seat in live)
            or live != tuple(seat for seat in seats if seat in live)):
        raise ValueError('APNAP participants require unique known seats in turn order')
    if turn_anchor is not None and (type(turn_anchor) is not str or turn_anchor not in seats):
        raise ValueError('APNAP turn anchor must identify a known physical seat')
    if not live:
        return ()
    start = seats.index(turn_anchor) if turn_anchor is not None else 0
    return tuple(seat for seat in (*seats[start:], *seats[:start]) if seat in live)


YieldInvalidationReason = Literal[
    "none",
    "phase",
    "draw",
    "action_change",
    "stack",
    "public_change",
]


class TurnPriorityHost(Protocol):
    """Narrow orchestration/query port consumed by the mutation owner."""

    state: GameState
    permissions: Any

    @property
    def active_seats(self) -> list[str]: ...

    def _stabilize(self) -> bool: ...

    def _active_cleanup_frame(self) -> dict[str, Any] | None: ...

    def _next_active_after(self, seat: str) -> str: ...

    def _priority_action_hints(self, seat: str) -> dict[str, Any]: ...

    def _prepare_stack_resolution(self) -> None: ...

    def _advance_step(self) -> None: ...

    def _play_land(self, seat: str, response: Mapping[str, Any]) -> None: ...

    def _turn_face_up(self, seat: str, response: Mapping[str, Any]) -> None: ...

    def _eliminate_players(
        self, seats: Sequence[str], *, reason: str
    ) -> None: ...

    def _semantic_pause_annotation(self) -> dict[str, Any] | None: ...

    def _enter_step(self) -> None: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class PriorityGrantPlan:
    """One validated priority grant, independent of mutable engine state."""

    seat: str
    priority_epoch: int
    cleanup_frame: bool = False

    def __post_init__(self) -> None:
        if type(self.seat) is not str or not self.seat:
            raise ValueError("Priority grant seat must be a nonempty string")
        if type(self.priority_epoch) is not int or self.priority_epoch < 1:
            raise ValueError("Priority epoch must be a positive exact integer")
        if type(self.cleanup_frame) is not bool:
            raise ValueError("Cleanup-frame state must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PriorityGrantPlan":
        expected = {"seat", "priority_epoch", "cleanup_frame"}
        if set(data) != expected:
            raise ValueError("Priority grant plan has an invalid shape")
        return cls(
            seat=data["seat"],
            priority_epoch=data["priority_epoch"],
            cleanup_frame=data["cleanup_frame"],
        )


@dataclass(frozen=True, slots=True)
class PriorityPassPlan:
    """Validated result of one seat passing priority."""

    seat: str
    passes: tuple[str, ...]
    next_seat: str | None
    round_complete: bool
    stack_waiting: bool

    def __post_init__(self) -> None:
        if type(self.seat) is not str or not self.seat:
            raise ValueError("Priority pass seat must be a nonempty string")
        if any(type(value) is not str or not value for value in self.passes):
            raise ValueError("Priority passes must be nonempty seat strings")
        if len(set(self.passes)) != len(self.passes):
            raise ValueError("Priority passes must be unique")
        if self.next_seat is not None and (
            type(self.next_seat) is not str or not self.next_seat
        ):
            raise ValueError("Next priority seat must be a nonempty string")
        if type(self.round_complete) is not bool:
            raise ValueError("Round-complete state must be boolean")
        if type(self.stack_waiting) is not bool:
            raise ValueError("Stack-waiting state must be boolean")
        if self.round_complete != (self.next_seat is None):
            raise ValueError(
                "A completed priority round cannot name a next seat"
            )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passes"] = list(self.passes)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PriorityPassPlan":
        expected = {
            "seat",
            "passes",
            "next_seat",
            "round_complete",
            "stack_waiting",
        }
        if set(data) != expected:
            raise ValueError("Priority pass plan has an invalid shape")
        passes = data["passes"]
        if not isinstance(passes, (list, tuple)):
            raise ValueError("Priority pass plan passes must be a sequence")
        return cls(
            seat=data["seat"],
            passes=tuple(passes),
            next_seat=data["next_seat"],
            round_complete=data["round_complete"],
            stack_waiting=data["stack_waiting"],
        )
