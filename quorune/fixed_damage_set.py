from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Protocol, Sequence

from .damage import (
    DamageBatchResult,
    DamageError,
    recipient_snapshot,
    resolve_damage_batch,
    source_snapshot,
)
from .damage_values import DamageProposal
from .affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    select_affected_permanents,
)
from .fixed_damage_set_model import (
    FixedDamageGroup,
    FixedDamageSetError,
    FixedDamageSetSpec,
    PermanentControllerRelation,
    PermanentDamageGroup,
    PlayerDamageGroup,
    PlayerDamageRelation,
    require_nonempty_string,
)
from .object_query import ObjectQueryResult
from .util import stable_json

_REASON_FIELD = "".join(("rea", "son"))


@dataclass(frozen=True, slots=True)
class FixedDamageSetRecipient:
    kind: str
    ref: str
    controller: str
    object_id: str | None = None
    logical_object_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"player", "permanent"}:
            raise FixedDamageSetError("Fixed damage recipient kind is invalid")
        for field, value in (
            ("ref", self.ref),
            ("controller", self.controller),
        ):
            require_nonempty_string(value, field=f"Recipient {field}")
        if self.kind == "player":
            if self.object_id is not None or self.logical_object_id is not None:
                raise FixedDamageSetError(
                    "Player damage recipients cannot carry object identity"
                )
        elif not self.object_id or not self.logical_object_id:
            raise FixedDamageSetError(
                "Permanent damage recipients require physical and logical identity"
            )

    @property
    def identity(self) -> str:
        return (
            f"player:{self.ref}"
            if self.kind == "player"
            else f"permanent:{self.logical_object_id}"
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "controller": self.controller,
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
        }


@dataclass(frozen=True, slots=True)
class FixedDamageSetSnapshot:
    spec: FixedDamageSetSpec
    recipients: tuple[FixedDamageSetRecipient, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.spec, FixedDamageSetSpec):
            raise FixedDamageSetError(
                "Fixed damage snapshots require a typed specification"
            )
        recipients = tuple(self.recipients)
        identities = [recipient.identity for recipient in recipients]
        if len(identities) != len(set(identities)):
            raise FixedDamageSetError(
                "Fixed damage snapshots require unique recipient identities"
            )
        object.__setattr__(self, "recipients", recipients)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "recipients": [recipient.to_dict() for recipient in self.recipients],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


class FixedDamageSetQuery(Protocol):
    def fixed_damage_active_seats(self) -> tuple[str, ...]: ...

    def fixed_damage_apnap_order(self) -> tuple[str, ...]: ...

    def fixed_damage_object_rows(
        self, actor: str
    ) -> tuple[ObjectQueryResult, ...]: ...


class FixedDamageSetHost(FixedDamageSetQuery, Protocol):
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


def snapshot_fixed_damage_set(
    query: FixedDamageSetQuery,
    *,
    actor: str,
    spec: FixedDamageSetSpec,
    source_ref: str | None = None,
) -> FixedDamageSetSnapshot:
    """Materialize one immutable simultaneous recipient set from public facts."""

    if not isinstance(spec, FixedDamageSetSpec):
        raise FixedDamageSetError(
            "Fixed damage recipient selection requires a typed specification"
        )
    active = tuple(query.fixed_damage_active_seats())
    order = tuple(query.fixed_damage_apnap_order())
    if (
        actor not in active
        or len(active) != len(set(active))
        or set(order) != set(active)
        or len(order) != len(active)
    ):
        raise FixedDamageSetError(
            "Fixed damage recipient selection requires a complete APNAP view"
        )
    order_index = {seat: index for index, seat in enumerate(order)}
    rows = tuple(query.fixed_damage_object_rows(actor))
    recipients: list[FixedDamageSetRecipient] = []
    seen: set[str] = set()

    def append(recipient: FixedDamageSetRecipient) -> None:
        if recipient.identity not in seen:
            seen.add(recipient.identity)
            recipients.append(recipient)

    for group in spec.groups:
        if isinstance(group, PlayerDamageGroup):
            for seat in order:
                if (
                    group.relation is PlayerDamageRelation.OPPONENTS
                    and seat == actor
                ):
                    continue
                append(
                    FixedDamageSetRecipient(
                        kind="player",
                        ref=seat,
                        controller=seat,
                    )
                )
            continue
        try:
            selected = select_affected_permanents(
                rows,
                AffectedPermanentSetSpec(
                    query=group.query,
                    controller_relation=group.controller_relation,
                    target_controller=group.target_controller,
                    exclude_source=group.exclude_source,
                ),
                actor=actor,
                active_seats=active,
                apnap_order=order,
                source_ref=source_ref,
            )
        except AffectedPermanentSetError as exc:
            raise FixedDamageSetError(str(exc)) from exc
        for row in selected:
            append(
                FixedDamageSetRecipient(
                    kind="permanent",
                    ref=row.ref,
                    controller=row.controller,
                    object_id=row.object_id,
                    logical_object_id=row.logical_object_id,
                )
            )
    recipients.sort(
        key=lambda recipient: (
            order_index[recipient.controller],
            recipient.identity,
        )
    )
    return FixedDamageSetSnapshot(spec=spec, recipients=tuple(recipients))


def resolve_fixed_damage_set(
    host: FixedDamageSetHost,
    *,
    actor: str,
    source_ref: str,
    amount: int,
    spec: FixedDamageSetSpec,
    reason: str,
    replacement_selections: Sequence[
        str | None | Mapping[str, object]
    ] = (),
    replacement_event_ids: Sequence[str] = (),
) -> DamageBatchResult:
    """Resolve one fixed set as one canonical simultaneous damage batch."""

    for field, value in (
        ("actor", actor),
        ("source", source_ref),
        (_REASON_FIELD, reason),
    ):
        require_nonempty_string(value, field=f"Fixed damage {field}")
    if type(amount) is not int or amount <= 0:
        raise FixedDamageSetError(
            "Fixed damage set amount must be a positive integer"
        )
    snapshot = snapshot_fixed_damage_set(
        host,
        actor=actor,
        spec=spec,
        source_ref=source_ref,
    )
    event_ids = tuple(replacement_event_ids)
    if any(type(value) is not str or not value for value in event_ids):
        raise FixedDamageSetError(
            "Fixed damage replacement event identities must be nonempty strings"
        )
    if event_ids and len(event_ids) != len(snapshot.recipients):
        raise FixedDamageSetError(
            "Fixed damage replacement event identity count is stale"
        )
    try:
        source = source_snapshot(host, source_ref, controller=actor)
        proposals: list[DamageProposal] = []
        for index, recipient in enumerate(snapshot.recipients):
            current = recipient_snapshot(host, recipient.ref, actor=actor)
            current_identity = (
                f"player:{current.ref}"
                if current.kind == "player"
                else f"permanent:{current.logical_object_id}"
            )
            if current_identity != recipient.identity:
                raise FixedDamageSetError(
                    "Fixed damage recipient snapshot became stale before commit"
                )
            proposals.append(
                DamageProposal(
                    proposal_id=(
                        event_ids[index]
                        if event_ids
                        else (
                            f"damage.fixed-set:{host.state.revision}:"
                            f"{host.state.event_sequence + 1}:{index}:"
                            f"{snapshot.fingerprint[:12]}"
                        )
                    ),
                    source=source,
                    recipient=current,
                    amount=amount,
                    combat=False,
                    reason=reason,
                )
            )
        result = resolve_damage_batch(
            host,
            tuple(proposals),
            replacement_selections=tuple(replacement_selections),
        )
    except DamageError as exc:
        raise FixedDamageSetError(str(exc)) from exc
    host._log(
        actor,
        "effect.damage.fixed_set",
        f"{source_ref} dealt fixed damage to {len(result.events)} recipient(s).",
        {
            "source": source_ref,
            "assigned_amount": amount,
            "recipient_count": len(snapshot.recipients),
            "snapshot_fingerprint": snapshot.fingerprint,
            "damage_events": [
                event.semantic_context() for event in result.events
            ],
            _REASON_FIELD: reason,
        },
        importance=2,
        changed_objects=result.changed_objects,
        changed_players=result.changed_players,
    )
    return result


__all__ = [
    "FixedDamageGroup",
    "FixedDamageSetError",
    "FixedDamageSetHost",
    "FixedDamageSetQuery",
    "FixedDamageSetRecipient",
    "FixedDamageSetSnapshot",
    "FixedDamageSetSpec",
    "PermanentControllerRelation",
    "PermanentDamageGroup",
    "PlayerDamageGroup",
    "PlayerDamageRelation",
    "resolve_fixed_damage_set",
    "snapshot_fixed_damage_set",
]
