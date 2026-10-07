from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from ..source_continuity import SourceContinuitySnapshot


class SemanticNodeError(ValueError):
    """A registered semantic node is malformed for the current rules view."""


@dataclass(frozen=True, slots=True)
class SemanticSourceContext:
    """Public identity of the stack object and its represented source."""

    stack_ref: str
    object_id: str | None = None
    logical_object_id: str | None = None
    card_ref: str | None = None
    duration_history: SourceContinuitySnapshot | None = None
    duration_resolution_timestamp: int | None = None

    def __post_init__(self) -> None:
        if self.duration_history is not None and not isinstance(self.duration_history, SourceContinuitySnapshot):
            raise SemanticNodeError("Duration source history must be a retained typed snapshot")
        if (self.duration_resolution_timestamp is not None
                and (type(self.duration_resolution_timestamp) is not int or self.duration_resolution_timestamp < 0)):
            raise SemanticNodeError("Duration resolution timestamp must be a nonnegative integer")
        if not self.stack_ref:
            raise SemanticNodeError("Semantic source stack identity is required")
        if (self.object_id is None) != (self.logical_object_id is None):
            raise SemanticNodeError(
                "Semantic source physical and logical identities are paired"
            )
        for value in (
            self.object_id,
            self.logical_object_id,
            self.card_ref,
        ):
            if value is not None and (type(value) is not str or not value):
                raise SemanticNodeError(
                    "Semantic source identities must be nonempty strings or null"
                )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "stack_ref": self.stack_ref,
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "card_ref": self.card_ref,
        }


def semantic_source_context(
    item: Any,
    cards: Mapping[str, Any],
) -> SemanticSourceContext:
    """Bind one stack item to trusted physical/logical source identity."""

    source_id = item.source_object_id or item.card_object_id or ""
    source = cards.get(source_id)
    logical_object_id = (
        str(item.context.get("source_logical_object_id") or "")
        if source is not None
        else ""
    ) or (source.logical_object_id if source is not None else None)
    return SemanticSourceContext(
        stack_ref=item.ref,
        object_id=source.object_id if source is not None else None,
        logical_object_id=logical_object_id,
        card_ref=source.ref if source is not None else None,
        duration_history=(
            SourceContinuitySnapshot.from_dict(item.context["control_duration_snapshot"])
            if item.context.get("control_duration_snapshot") is not None else None
        ),
        duration_resolution_timestamp=item.context.get("control_duration_resolution_timestamp"),
    )


@dataclass(frozen=True, slots=True)
class ReadOnlyRulesQuery:
    """Minimum immutable rules facts exposed to generic semantic handlers."""

    seats: tuple[str, ...]
    active_seats: tuple[str, ...]
    apnap_order: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.seats or len(self.seats) != len(set(self.seats)):
            raise SemanticNodeError("Rules query seats must be unique and nonempty")
        if any(seat not in self.seats for seat in self.active_seats):
            raise SemanticNodeError("Active seats must belong to the game")
        if len(self.active_seats) != len(set(self.active_seats)):
            raise SemanticNodeError("Active seats must be unique")
        if (
            len(self.apnap_order) != len(self.active_seats)
            or len(self.apnap_order) != len(set(self.apnap_order))
            or set(self.apnap_order) != set(self.active_seats)
        ):
            raise SemanticNodeError(
                "APNAP order must contain each active seat exactly once"
            )

    def require_known_seat(self, seat: str) -> str:
        if seat not in self.seats:
            raise SemanticNodeError(f"Unknown seat {seat!r}")
        return seat

    def require_active_seat(self, seat: str) -> str:
        self.require_known_seat(seat)
        if seat not in self.active_seats:
            raise SemanticNodeError(f"{seat} is no longer in the game")
        return seat


@dataclass(frozen=True, slots=True)
class ReadOnlyHandlerContext:
    """Handler input that deliberately excludes GameState and hidden zones."""

    actor: str
    default_reason: str
    query: ReadOnlyRulesQuery
    source: SemanticSourceContext | None = None

    @classmethod
    def from_sequences(
        cls,
        *,
        actor: str,
        default_reason: str,
        seats: Iterable[str],
        active_seats: Iterable[str],
        apnap_order: Iterable[str],
        source: SemanticSourceContext | None = None,
    ) -> "ReadOnlyHandlerContext":
        return cls(
            actor=actor,
            default_reason=default_reason,
            query=ReadOnlyRulesQuery(
                seats=tuple(seats),
                active_seats=tuple(active_seats),
                apnap_order=tuple(apnap_order),
            ),
            source=source,
        )

    def __post_init__(self) -> None:
        self.query.require_known_seat(self.actor)
        if not self.default_reason:
            raise SemanticNodeError("A semantic effect requires a reason")
        if self.source is not None and not isinstance(
            self.source, SemanticSourceContext
        ):
            raise SemanticNodeError(
                "Semantic handler source context must be typed"
            )
