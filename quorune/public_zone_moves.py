from __future__ import annotations

"""Typed public-origin zone-move effects.

This module selects immutable public object sets and delegates every mutation,
replacement, incarnation, trigger, and journal transition to the canonical
``ZoneTransitionOwner``.
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .object_predicate import ObjectQueryError, ObjectQuerySpec
from .object_query import ObjectQueryResult, query_objects
from .rules.single_object_zone_transition import (
    SingleObjectDestination,
    SingleObjectOrigin,
    SingleObjectZoneTransitionError,
    commit_prevalidated_single_object_zone_transition,
    prepare_single_object_zone_transition,
    request_for_card,
)
from .util import stable_json


FIXED_OWNER_ZONE_MOVE_CAPABILITY = "zone.single_owner_move.fixed_destination"
FIXED_OWNER_ZONE_MOVE_MECHANIC = "fixed-owner-zone-move"


class PublicZoneMoveError(ValueError):
    """A public zone-move descriptor, snapshot, or request is invalid."""


class PublicZoneOrigin(str, Enum):
    BATTLEFIELD = "battlefield"
    GRAVEYARD = "graveyard"


class PublicZoneDestination(str, Enum):
    EXILE = "exile"
    OWNER_HAND = "hand"


class PublicZoneRelationAxis(str, Enum):
    CONTROLLER = "controller"
    OWNER = "owner"


class PublicZoneSeatRelation(str, Enum):
    ANY = "any"
    ACTOR = "actor"
    OPPONENTS = "opponents"
    TARGET_PLAYER = "target_player"


_SET_FIELDS = frozenset(
    {
        "schema_version",
        "origin",
        "destination",
        "relation_axis",
        "seat_relation",
        "target_seat",
        "exclude_source",
        "query",
    }
)


def _nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise PublicZoneMoveError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class PublicZoneMoveSetSpec:
    """One closed public-origin object set and requested destination."""

    query: ObjectQuerySpec
    origin: PublicZoneOrigin
    destination: PublicZoneDestination
    relation_axis: PublicZoneRelationAxis = PublicZoneRelationAxis.CONTROLLER
    seat_relation: PublicZoneSeatRelation = PublicZoneSeatRelation.ANY
    target_seat: str | None = None
    exclude_source: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise PublicZoneMoveError(
                "Unsupported public zone-move set schema version"
            )
        if not isinstance(self.query, ObjectQuerySpec):
            raise PublicZoneMoveError(
                "Public zone-move sets require a typed object query"
            )
        if not isinstance(self.origin, PublicZoneOrigin) or not isinstance(
            self.destination, PublicZoneDestination
        ):
            raise PublicZoneMoveError(
                "Public zone-move origin or destination is unsupported"
            )
        if not isinstance(self.relation_axis, PublicZoneRelationAxis) or not isinstance(
            self.seat_relation, PublicZoneSeatRelation
        ):
            raise PublicZoneMoveError(
                "Public zone-move seat relation is unsupported"
            )
        if self.query.zones != (self.origin.value,):
            raise PublicZoneMoveError(
                "Public zone-move query must name its exact origin"
            )
        if self.query.owner is not None or self.query.controller is not None:
            raise PublicZoneMoveError(
                "Public zone-move sets use the typed seat relation"
            )
        if self.query.known_to_actor is not None or self.query.exclude_ref is not None:
            raise PublicZoneMoveError(
                "Public zone-move queries cannot use knowledge or reference shortcuts"
            )
        if self.query.include_phased_out:
            raise PublicZoneMoveError(
                "Public zone-move sets exclude phased-out objects"
            )
        if self.origin is PublicZoneOrigin.GRAVEYARD:
            if self.destination is not PublicZoneDestination.EXILE:
                raise PublicZoneMoveError(
                    "The represented graveyard set moves only to exile"
                )
            if self.relation_axis is not PublicZoneRelationAxis.OWNER:
                raise PublicZoneMoveError(
                    "Graveyard sets use owner-relative seats"
                )
        if type(self.exclude_source) is not bool:
            raise PublicZoneMoveError(
                "Public zone-move source exclusion must be boolean"
            )
        if self.origin is PublicZoneOrigin.GRAVEYARD and self.exclude_source:
            raise PublicZoneMoveError(
                "Graveyard set grammar does not exclude the resolving source"
            )
        if self.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER:
            _nonempty(self.target_seat, field="Public zone-move target seat")
        elif self.target_seat is not None:
            raise PublicZoneMoveError(
                "Only target-player sets accept a target seat"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "origin": self.origin.value,
            "destination": self.destination.value,
            "relation_axis": self.relation_axis.value,
            "seat_relation": self.seat_relation.value,
            "target_seat": self.target_seat,
            "exclude_source": self.exclude_source,
            "query": self.query.canonical_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicZoneMoveSetSpec":
        if not isinstance(value, Mapping) or frozenset(value) != _SET_FIELDS:
            raise PublicZoneMoveError(
                "Public zone-move set fields are incomplete or unknown"
            )
        try:
            return cls(
                query=ObjectQuerySpec.from_dict(value["query"]),
                origin=PublicZoneOrigin(value["origin"]),
                destination=PublicZoneDestination(value["destination"]),
                relation_axis=PublicZoneRelationAxis(value["relation_axis"]),
                seat_relation=PublicZoneSeatRelation(value["seat_relation"]),
                target_seat=value["target_seat"],
                exclude_source=value["exclude_source"],
                schema_version=value["schema_version"],
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ObjectQueryError,
        ) as exc:
            if isinstance(exc, PublicZoneMoveError):
                raise
            raise PublicZoneMoveError(
                "Public zone-move set descriptor is malformed"
            ) from exc

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class PublicZoneMoveObject:
    object_id: str
    logical_object_id: str
    ref: str
    owner: str
    controller: str

    def __post_init__(self) -> None:
        for field, value in (
            ("object ID", self.object_id),
            ("logical object ID", self.logical_object_id),
            ("reference", self.ref),
            ("owner", self.owner),
            ("controller", self.controller),
        ):
            _nonempty(value, field=f"Public zone-move {field}")


@dataclass(frozen=True, slots=True)
class PublicZoneMoveSnapshot:
    spec: PublicZoneMoveSetSpec
    objects: tuple[PublicZoneMoveObject, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.spec, PublicZoneMoveSetSpec):
            raise PublicZoneMoveError(
                "Public zone-move snapshots require a typed set"
            )
        values = tuple(self.objects)
        if any(not isinstance(value, PublicZoneMoveObject) for value in values):
            raise PublicZoneMoveError(
                "Public zone-move snapshots require typed objects"
            )
        object_ids = tuple(value.object_id for value in values)
        logical_ids = tuple(value.logical_object_id for value in values)
        if len(object_ids) != len(set(object_ids)) or len(logical_ids) != len(
            set(logical_ids)
        ):
            raise PublicZoneMoveError(
                "Public zone-move snapshots require unique identities"
            )
        object.__setattr__(self, "objects", values)

    @property
    def fingerprint(self) -> str:
        payload = {
            "spec": self.spec.to_dict(),
            "objects": [
                {
                    "object_id": value.object_id,
                    "logical_object_id": value.logical_object_id,
                    "ref": value.ref,
                    "owner": value.owner,
                    "controller": value.controller,
                }
                for value in self.objects
            ],
        }
        return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


class PublicZoneMoveQuery(Protocol):
    def public_zone_move_active_seats(self) -> tuple[str, ...]: ...

    def public_zone_move_apnap_order(self) -> tuple[str, ...]: ...

    def public_zone_move_object_rows(
        self, actor: str
    ) -> tuple[ObjectQueryResult, ...]: ...


class PublicZoneMoveHost(PublicZoneMoveQuery, Protocol):
    state: Any

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str] | None = None,
    ) -> Any: ...

    def move_card(self, object_id: str, destination: str, **kwargs: Any) -> Any: ...

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


def _relation_seat(
    row: ObjectQueryResult,
    spec: PublicZoneMoveSetSpec,
) -> str:
    return (
        row.controller
        if spec.relation_axis is PublicZoneRelationAxis.CONTROLLER
        else row.owner
    )


def select_public_zone_move_objects(
    rows: Iterable[ObjectQueryResult],
    spec: PublicZoneMoveSetSpec,
    *,
    actor: str,
    active_seats: Iterable[str],
    apnap_order: Iterable[str],
    source_ref: str | None = None,
) -> tuple[ObjectQueryResult, ...]:
    """Select and APNAP-order one immutable public-origin object set."""

    if not isinstance(spec, PublicZoneMoveSetSpec):
        raise PublicZoneMoveError(
            "Public zone-move selection requires a typed set"
        )
    _nonempty(actor, field="Public zone-move actor")
    active = tuple(active_seats)
    order = tuple(apnap_order)
    if (
        actor not in active
        or len(active) != len(set(active))
        or len(order) != len(active)
        or set(order) != set(active)
    ):
        raise PublicZoneMoveError(
            "Public zone-move selection requires a complete APNAP view"
        )
    if spec.exclude_source:
        _nonempty(source_ref, field="Public zone-move source")
    order_index = {seat: index for index, seat in enumerate(order)}
    selected = tuple(
        row
        for row in query_objects(tuple(rows), spec.query)
        if row.zone == spec.origin.value
    )
    if spec.seat_relation is PublicZoneSeatRelation.ACTOR:
        selected = tuple(
            row for row in selected if _relation_seat(row, spec) == actor
        )
    elif spec.seat_relation is PublicZoneSeatRelation.OPPONENTS:
        selected = tuple(
            row for row in selected if _relation_seat(row, spec) != actor
        )
    elif spec.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER:
        if spec.target_seat not in active:
            raise PublicZoneMoveError(
                "Public zone-move target player is no longer active"
            )
        selected = tuple(
            row
            for row in selected
            if _relation_seat(row, spec) == spec.target_seat
        )
    if spec.exclude_source:
        selected = tuple(row for row in selected if row.ref != source_ref)
    if any(
        _relation_seat(row, spec) not in order_index
        or not row.object_id
        or not row.logical_object_id
        or not row.ref
        or row.phased_out
        for row in selected
    ):
        raise PublicZoneMoveError(
            "Public zone-move query returned an invalid identity"
        )
    by_logical_id: dict[str, ObjectQueryResult] = {}
    for row in selected:
        previous = by_logical_id.get(row.logical_object_id)
        if previous is not None and previous.object_id != row.object_id:
            raise PublicZoneMoveError(
                "Public zone-move query repeated one logical object"
            )
        by_logical_id[row.logical_object_id] = row
    return tuple(
        sorted(
            by_logical_id.values(),
            key=lambda row: (
                order_index[_relation_seat(row, spec)],
                row.logical_object_id,
                row.object_id,
                row.ref,
            ),
        )
    )


def snapshot_public_zone_move_set(
    query: PublicZoneMoveQuery,
    *,
    actor: str,
    spec: PublicZoneMoveSetSpec,
    source_ref: str | None = None,
) -> PublicZoneMoveSnapshot:
    selected = select_public_zone_move_objects(
        query.public_zone_move_object_rows(actor),
        spec,
        actor=actor,
        active_seats=query.public_zone_move_active_seats(),
        apnap_order=query.public_zone_move_apnap_order(),
        source_ref=source_ref,
    )
    return PublicZoneMoveSnapshot(
        spec=spec,
        objects=tuple(
            PublicZoneMoveObject(
                object_id=row.object_id,
                logical_object_id=row.logical_object_id,
                ref=row.ref,
                owner=row.owner,
                controller=row.controller,
            )
            for row in selected
        ),
    )


def resolve_public_zone_move_set(
    host: PublicZoneMoveHost,
    *,
    actor: str,
    spec: PublicZoneMoveSetSpec,
    reason: str,
    source_ref: str | None = None,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> PublicZoneMoveSnapshot:
    """Resolve a fixed public set through the canonical simultaneous owner."""

    _nonempty(actor, field="Public zone-move actor")
    _nonempty(reason, field="Public zone-move reason")
    snapshot = snapshot_public_zone_move_set(
        host,
        actor=actor,
        spec=spec,
        source_ref=source_ref,
    )
    for value in snapshot.objects:
        card = host.state.cards.get(value.object_id)
        if (
            card is None
            or card.logical_object_id != value.logical_object_id
            or card.ref != value.ref
            or card.owner != value.owner
            or card.controller != value.controller
            or card.zone != spec.origin.value
            or card.phased_out
            or (
                spec.origin is PublicZoneOrigin.GRAVEYARD
                and not bool(getattr(card, "is_card_object", True))
            )
        ):
            raise PublicZoneMoveError(
                "Public zone-move snapshot became stale before commit"
            )
    if snapshot.objects:
        from .zone_transitions import ZoneTransitionOwner

        ZoneTransitionOwner(host).move_cards_simultaneously(
            tuple(
                (value.object_id, spec.destination.value)
                for value in snapshot.objects
            ),
            reason=reason,
            log=False,
            replacement_selections=replacement_selections,
        )
    host._log(
        actor,
        "effect.public_zone_move_set",
        f"Moved {len(snapshot.objects)} public object(s) toward {spec.destination.value}.",
        {
            "snapshot_fingerprint": snapshot.fingerprint,
            "affected_count": len(snapshot.objects),
            "origin": spec.origin.value,
            "requested_destination": spec.destination.value,
            "reason": reason,
        },
        importance=2,
        changed_objects=tuple(value.object_id for value in snapshot.objects),
        changed_players=tuple(
            sorted({value.owner for value in snapshot.objects})
        ),
    )
    return snapshot


def exile_public_graveyard_card(
    host: PublicZoneMoveHost,
    object_ref: str,
    *,
    actor: str,
    reason: str,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> str:
    """Move one revalidated physical card from a public graveyard to exile."""

    card = host._resolve_object(actor, object_ref, zones={"graveyard"})
    if not bool(getattr(card, "is_card_object", True)):
        raise PublicZoneMoveError(
            "Public graveyard exile requires a physical card"
        )
    try:
        plan = prepare_single_object_zone_transition(
            host,
            request_for_card(card),
            actor=actor,
            reason=reason,
            requested_destination=SingleObjectDestination.EXILE,
            expected_origin=SingleObjectOrigin.GRAVEYARD,
            replacement_selections=replacement_selections,
        )
        result = commit_prevalidated_single_object_zone_transition(host, plan)
    except SingleObjectZoneTransitionError as exc:
        raise PublicZoneMoveError(str(exc)) from exc
    host._log(
        actor,
        "card.exile_from_graveyard",
        f"{result.object_ref} moved from a graveyard toward exile.",
        {
            "object": result.object_ref,
            "owner": result.owner,
            "origin": result.origin.value,
            "requested_destination": SingleObjectDestination.EXILE.value,
            "destination": result.actual_destination,
            "reason": reason,
        },
        importance=2,
        changed_objects=(result.object_id,),
        changed_players=(result.owner,),
    )
    return result.object_ref


__all__ = [
    "exile_public_graveyard_card",
    "FIXED_OWNER_ZONE_MOVE_CAPABILITY",
    "FIXED_OWNER_ZONE_MOVE_MECHANIC",
    "PublicZoneDestination",
    "PublicZoneMoveError",
    "PublicZoneMoveHost",
    "PublicZoneMoveObject",
    "PublicZoneMoveQuery",
    "PublicZoneMoveSetSpec",
    "PublicZoneMoveSnapshot",
    "PublicZoneOrigin",
    "PublicZoneRelationAxis",
    "PublicZoneSeatRelation",
    "resolve_public_zone_move_set",
    "select_public_zone_move_objects",
    "snapshot_public_zone_move_set",
]
