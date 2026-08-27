from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Protocol, Sequence

from .affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    select_affected_permanents,
)
from .destruction import (
    DestructionCause,
    DestructionError,
    DestructionRequest,
    DestructionResult,
    commit_destruction_plan,
    prepare_destructions,
)
from .object_query import ObjectQueryResult
from .util import stable_json

_REASON_FIELD = "reason"


class DestructionSetError(ValueError):
    """A mass-destruction snapshot or transaction is invalid."""


def _nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise DestructionSetError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class DestructionSetPermanent:
    object_id: str
    logical_object_id: str
    ref: str
    controller: str

    def __post_init__(self) -> None:
        for field, value in (
            ("object ID", self.object_id),
            ("logical object ID", self.logical_object_id),
            ("reference", self.ref),
            ("controller", self.controller),
        ):
            _nonempty(value, field=f"Destruction set {field}")

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "ref": self.ref,
            "controller": self.controller,
        }


@dataclass(frozen=True, slots=True)
class DestructionSetSnapshot:
    spec: AffectedPermanentSetSpec
    permanents: tuple[DestructionSetPermanent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise DestructionSetError(
                "Destruction snapshots require a typed affected set"
            )
        values = tuple(self.permanents)
        if any(not isinstance(value, DestructionSetPermanent) for value in values):
            raise DestructionSetError(
                "Destruction snapshots require typed permanents"
            )
        logical_ids = tuple(value.logical_object_id for value in values)
        object_ids = tuple(value.object_id for value in values)
        if len(logical_ids) != len(set(logical_ids)) or len(object_ids) != len(
            set(object_ids)
        ):
            raise DestructionSetError(
                "Destruction snapshots require unique permanent identities"
            )
        object.__setattr__(self, "permanents", values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "permanents": [value.to_dict() for value in self.permanents],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


class DestructionSetQuery(Protocol):
    def affected_permanent_active_seats(self) -> tuple[str, ...]: ...

    def affected_permanent_apnap_order(self) -> tuple[str, ...]: ...

    def affected_permanent_object_rows(
        self, actor: str
    ) -> tuple[ObjectQueryResult, ...]: ...


class DestructionSetHost(DestructionSetQuery, Protocol):
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


def snapshot_destruction_set(
    query: DestructionSetQuery,
    *,
    actor: str,
    spec: AffectedPermanentSetSpec,
    source_ref: str | None = None,
) -> DestructionSetSnapshot:
    """Freeze the complete affected set before destruction preflight."""

    try:
        selected = select_affected_permanents(
            query.affected_permanent_object_rows(actor),
            spec,
            actor=actor,
            active_seats=query.affected_permanent_active_seats(),
            apnap_order=query.affected_permanent_apnap_order(),
            source_ref=source_ref,
        )
    except AffectedPermanentSetError as exc:
        raise DestructionSetError(str(exc)) from exc
    return DestructionSetSnapshot(
        spec=spec,
        permanents=tuple(
            DestructionSetPermanent(
                object_id=row.object_id,
                logical_object_id=row.logical_object_id,
                ref=row.ref,
                controller=row.controller,
            )
            for row in selected
        ),
    )


def resolve_destruction_set(
    host: DestructionSetHost,
    *,
    actor: str,
    spec: AffectedPermanentSetSpec,
    reason: str,
    source_ref: str | None = None,
    regeneration_prohibited: bool = False,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> DestructionResult:
    """Resolve one fixed affected set through the canonical destruction owner."""

    _nonempty(actor, field="Destruction set actor")
    _nonempty(reason, field="Destruction set reason")
    if type(regeneration_prohibited) is not bool:
        raise DestructionSetError(
            "Destruction-set regeneration prohibition must be boolean"
        )
    snapshot = snapshot_destruction_set(
        host,
        actor=actor,
        spec=spec,
        source_ref=source_ref,
    )
    try:
        plan = prepare_destructions(
            host,
            tuple(
                DestructionRequest(
                    object_id=value.object_id,
                    logical_object_id=value.logical_object_id,
                )
                for value in snapshot.permanents
            ),
            cause=DestructionCause.EFFECT,
            actor=actor,
            reason=reason,
            regeneration_prohibited=regeneration_prohibited,
            event_order=tuple(
                value.object_id for value in snapshot.permanents
            ),
            replacement_selections=replacement_selections,
        )
        result = commit_destruction_plan(host, plan)
    except DestructionError as exc:
        raise DestructionSetError(str(exc)) from exc
    host._log(
        actor,
        "effect.permanent.destroy_set",
        f"Destroyed {len(result.destroyed_object_ids)} permanent(s) from a fixed set.",
        {
            "snapshot_fingerprint": snapshot.fingerprint,
            "affected_count": len(snapshot.permanents),
            "destroyed_count": len(result.destroyed_object_ids),
            "shielded_count": len(result.shielded_object_ids),
            "indestructible_count": len(result.indestructible_object_ids),
            _REASON_FIELD: reason,
        },
        importance=2,
        changed_objects=result.destroyed_object_ids,
        changed_players=tuple(
            sorted({value.controller for value in snapshot.permanents})
        ),
    )
    return result


__all__ = [
    "DestructionSetError",
    "DestructionSetHost",
    "DestructionSetPermanent",
    "DestructionSetQuery",
    "DestructionSetSnapshot",
    "resolve_destruction_set",
    "snapshot_destruction_set",
]
