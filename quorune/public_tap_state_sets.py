from __future__ import annotations

"""Resolution ownership for one immutable public tap-state set."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    select_affected_permanents,
)
from .object_query import ObjectQueryResult
from .tap_state import set_permanent_tapped


class PublicTapStateSetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SetPublicPermanentsTappedIntent:
    actor: str
    spec: AffectedPermanentSetSpec
    tapped: bool
    reason: str
    source_ref: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.actor) is not str
            or not self.actor
            or not isinstance(self.spec, AffectedPermanentSetSpec)
            or type(self.tapped) is not bool
            or type(self.reason) is not str
            or not self.reason
            or (
                self.source_ref is not None
                and (type(self.source_ref) is not str or not self.source_ref)
            )
            or (self.spec.exclude_source and self.source_ref is None)
        ):
            raise ValueError("Public-set tap-state intent is malformed")


@dataclass(frozen=True, slots=True)
class PublicTapStatePermanent:
    object_id: str
    logical_object_id: str
    ref: str
    controller: str


class PublicTapStateSetHost(Protocol):
    state: Any

    def affected_permanent_active_seats(self) -> tuple[str, ...]: ...

    def affected_permanent_apnap_order(self) -> tuple[str, ...]: ...

    def affected_permanent_object_rows(
        self, actor: str
    ) -> tuple[ObjectQueryResult, ...]: ...

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


def resolve_public_tap_state_set(
    host: PublicTapStateSetHost,
    *,
    actor: str,
    spec: AffectedPermanentSetSpec,
    tapped: bool,
    reason: str,
    source_ref: str | None = None,
) -> tuple[str, ...]:
    if type(actor) is not str or not actor or type(reason) is not str or not reason:
        raise PublicTapStateSetError(
            "Public tap-state sets require actor and reason"
        )
    if type(tapped) is not bool:
        raise PublicTapStateSetError("Public tap-state result must be boolean")
    try:
        selected = select_affected_permanents(
            host.affected_permanent_object_rows(actor),
            spec,
            actor=actor,
            active_seats=host.affected_permanent_active_seats(),
            apnap_order=host.affected_permanent_apnap_order(),
            source_ref=source_ref,
        )
    except AffectedPermanentSetError as exc:
        raise PublicTapStateSetError(str(exc)) from exc
    snapshot = tuple(
        PublicTapStatePermanent(
            object_id=row.object_id,
            logical_object_id=row.logical_object_id,
            ref=row.ref,
            controller=row.controller,
        )
        for row in selected
    )
    for value in snapshot:
        card = host.state.cards.get(value.object_id)
        if (
            card is None
            or card.zone != "battlefield"
            or card.logical_object_id != value.logical_object_id
            or card.ref != value.ref
            or card.controller != value.controller
            or card.phased_out
        ):
            raise PublicTapStateSetError(
                "Public tap-state set became stale before commit"
            )
    results = tuple(
        set_permanent_tapped(
            host,
            value.ref,
            actor=actor,
            tapped=tapped,
            reason=reason,
            logical_object_id=value.logical_object_id,
        )
        for value in snapshot
    )
    host._log(
        actor,
        "effect.permanent.tap_state_set",
        f"Processed {len(snapshot)} permanent tap state(s) from a fixed set.",
        {
            "affected_count": len(snapshot),
            "tapped": tapped,
            "reason": reason,
        },
        importance=2,
        changed_objects=tuple(value.object_id for value in snapshot),
        changed_players=tuple(sorted({value.controller for value in snapshot})),
    )
    return results


__all__ = [
    "PublicTapStateSetError",
    "SetPublicPermanentsTappedIntent",
    "resolve_public_tap_state_set",
]
