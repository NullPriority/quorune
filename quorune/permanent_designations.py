from __future__ import annotations

"""Typed ownership for public noncopiable permanent designations.

CR 701.37b's monstrous value, CR 702.112b's renowned marker, and CR 716.2b's
Class level are stored on the current logical object, survive control changes
and phasing, and are cleared only by the canonical zone-change owner.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


class PermanentDesignationError(ValueError):
    """A permanent designation request is malformed or stale."""


class PermanentDesignationHost(Protocol):
    state: Any

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> None: ...

    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        **kwargs: Any,
    ) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class BecomeMonstrousRequest:
    """One identity-pinned CR 701.37 designation transition."""

    object_id: str
    object_ref: str
    logical_object_id: str
    value: int
    actor: str
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.object_id,
                self.object_ref,
                self.logical_object_id,
                self.actor,
                self.reason,
            )
        ):
            raise PermanentDesignationError(
                "Monstrous designation identity and provenance are required"
            )
        if type(self.value) is not int or self.value < 0:
            raise PermanentDesignationError(
                "Monstrosity requires an exact nonnegative value"
            )


@dataclass(frozen=True, slots=True)
class MonstrousDesignationResult:
    object_ref: str
    logical_object_id: str
    value: int | None
    changed: bool


@dataclass(frozen=True, slots=True)
class BecomeRenownedRequest:
    """One identity-pinned CR 702.112 designation transition."""

    object_id: str
    object_ref: str
    logical_object_id: str
    actor: str
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.object_id,
                self.object_ref,
                self.logical_object_id,
                self.actor,
                self.reason,
            )
        ):
            raise PermanentDesignationError(
                "Renowned designation identity and provenance are required"
            )


@dataclass(frozen=True, slots=True)
class RenownedDesignationResult:
    object_ref: str
    logical_object_id: str
    changed: bool


@dataclass(frozen=True, slots=True)
class AdvanceClassLevelRequest:
    """One identity-pinned CR 716.2a-b level transition."""

    object_id: str
    object_ref: str
    logical_object_id: str
    level: int
    actor: str
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.object_id,
                self.object_ref,
                self.logical_object_id,
                self.actor,
                self.reason,
            )
        ):
            raise PermanentDesignationError(
                "Class level identity and provenance are required"
            )
        if type(self.level) is not int or self.level not in {2, 3}:
            raise PermanentDesignationError(
                "Class level transitions support exactly levels 2 and 3"
            )


@dataclass(frozen=True, slots=True)
class ClassLevelResult:
    object_ref: str
    logical_object_id: str
    level: int | None
    changed: bool


def current_class_level(card: Any) -> int:
    """Return CR 716.2d's effective level for one permanent object."""

    value = getattr(card, "class_level", None)
    if value is None:
        return 1
    if type(value) is not int or value not in {2, 3}:
        raise PermanentDesignationError(
            "The permanent's Class level designation is malformed"
        )
    return value


def permanent_numeric_designations(card: Any) -> dict[str, int]:
    """Expose typed public numeric designations to shared applicability."""

    return {"class_level": current_class_level(card)}


def advance_class_level(
    host: PermanentDesignationHost,
    request: AdvanceClassLevelRequest,
) -> ClassLevelResult:
    """Apply a Class level to the exact resolving source incarnation."""

    if not isinstance(request, AdvanceClassLevelRequest):
        raise PermanentDesignationError(
            "Class level advancement requires a typed request"
        )
    card = host.state.cards.get(request.object_id)
    if (
        card is None
        or card.ref != request.object_ref
        or card.logical_object_id != request.logical_object_id
        or card.zone != "battlefield"
        or card.phased_out
    ):
        return ClassLevelResult(
            object_ref=request.object_ref,
            logical_object_id=request.logical_object_id,
            level=None,
            changed=False,
        )
    prior = current_class_level(card)
    if prior == request.level:
        return ClassLevelResult(
            object_ref=card.ref,
            logical_object_id=card.logical_object_id,
            level=prior,
            changed=False,
        )
    card.class_level = request.level
    details = {
        "object": card.ref,
        "logical_object_id": card.logical_object_id,
        "controller": card.controller,
        "previous_level": prior,
        "level": request.level,
        "reason": request.reason,
    }
    host._log(
        request.actor,
        "permanent.class_level",
        f"{card.ref} became level {request.level}.",
        details,
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[card.controller],
    )
    host._dispatch_semantic_event(
        "permanent.class_level_changed",
        details,
        sources=(card,),
    )
    return ClassLevelResult(
        object_ref=card.ref,
        logical_object_id=card.logical_object_id,
        level=request.level,
        changed=prior != request.level,
    )


def become_monstrous(
    host: PermanentDesignationHost,
    request: BecomeMonstrousRequest,
) -> MonstrousDesignationResult:
    """Apply one CR 701.37a-b transition without copying or rediscovery.

    A source that left the battlefield or became a new logical object cannot
    be affected by the old resolving ability.  That is an ordinary impossible
    instruction, so the request becomes a deterministic no-op rather than
    finding a new object through its public reference.
    """

    if not isinstance(request, BecomeMonstrousRequest):
        raise PermanentDesignationError(
            "Monstrous designation requires a typed request"
        )
    card = host.state.cards.get(request.object_id)
    if (
        card is None
        or card.ref != request.object_ref
        or card.logical_object_id != request.logical_object_id
        or card.zone != "battlefield"
        or card.phased_out
    ):
        return MonstrousDesignationResult(
            object_ref=request.object_ref,
            logical_object_id=request.logical_object_id,
            value=None,
            changed=False,
        )
    if card.monstrous_value is not None:
        if (
            type(card.monstrous_value) is not int
            or card.monstrous_value < 0
        ):
            raise PermanentDesignationError(
                "The permanent's monstrous designation is malformed"
            )
        return MonstrousDesignationResult(
            object_ref=card.ref,
            logical_object_id=card.logical_object_id,
            value=card.monstrous_value,
            changed=False,
        )

    card.monstrous_value = request.value
    details = {
        "object": card.ref,
        "logical_object_id": card.logical_object_id,
        "value": request.value,
        "controller": card.controller,
        "reason": request.reason,
    }
    host._log(
        request.actor,
        "permanent.monstrous",
        f"{card.ref} became monstrous.",
        details,
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[card.controller],
    )
    host._dispatch_semantic_event(
        "permanent.becomes_monstrous",
        details,
        sources=(card,),
    )
    return MonstrousDesignationResult(
        object_ref=card.ref,
        logical_object_id=card.logical_object_id,
        value=request.value,
        changed=True,
    )


def become_renowned(
    host: PermanentDesignationHost,
    request: BecomeRenownedRequest,
) -> RenownedDesignationResult:
    """Apply one CR 702.112a-b transition to the pinned logical object."""

    if not isinstance(request, BecomeRenownedRequest):
        raise PermanentDesignationError(
            "Renowned designation requires a typed request"
        )
    card = host.state.cards.get(request.object_id)
    if (
        card is None
        or card.ref != request.object_ref
        or card.logical_object_id != request.logical_object_id
        or card.zone != "battlefield"
        or card.phased_out
    ):
        return RenownedDesignationResult(
            object_ref=request.object_ref,
            logical_object_id=request.logical_object_id,
            changed=False,
        )
    if type(card.renowned) is not bool:
        raise PermanentDesignationError(
            "The permanent's renowned designation is malformed"
        )
    if card.renowned:
        return RenownedDesignationResult(
            object_ref=card.ref,
            logical_object_id=card.logical_object_id,
            changed=False,
        )

    card.renowned = True
    details = {
        "object": card.ref,
        "logical_object_id": card.logical_object_id,
        "controller": card.controller,
        "reason": request.reason,
    }
    host._log(
        request.actor,
        "permanent.renowned",
        f"{card.ref} became renowned.",
        details,
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[card.controller],
    )
    host._dispatch_semantic_event(
        "permanent.becomes_renowned",
        details,
        sources=(card,),
    )
    return RenownedDesignationResult(
        object_ref=card.ref,
        logical_object_id=card.logical_object_id,
        changed=True,
    )


__all__ = [
    "AdvanceClassLevelRequest",
    "BecomeMonstrousRequest",
    "BecomeRenownedRequest",
    "ClassLevelResult",
    "MonstrousDesignationResult",
    "PermanentDesignationError",
    "PermanentDesignationHost",
    "RenownedDesignationResult",
    "advance_class_level",
    "become_monstrous",
    "become_renowned",
    "current_class_level",
    "permanent_numeric_designations",
]
