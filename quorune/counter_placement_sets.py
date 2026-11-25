from __future__ import annotations

"""Immutable affected-set selection for fixed permanent counter effects."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from typing import Any, Protocol, Sequence

from .affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
    select_affected_permanents,
)
from .counter_placement import (
    CounterPlacementError,
    CounterPlacementHost,
    CounterPlacementRequest,
    CounterPlacementResult,
    place_counters,
)
from .object_query import ObjectQueryResult
from .object_predicate import ObjectQuerySpec
from .util import stable_json


PAIRED_PLANESWALKER_LOYALTY_FIELD = "paired_other_planeswalker_loyalty_version"
PAIRED_PLANESWALKER_LOYALTY_CAPABILITY = "counter.producer.paired_creature_and_planeswalker"


def controlled_counter_subjects(card_type, *, exclude_source=False):
    return AffectedPermanentSetSpec(
        query=ObjectQuerySpec(zones=("battlefield",), types_all=(card_type,)),
        controller_relation=PermanentControllerRelation.ACTOR, exclude_source=exclude_source,
    )


def paired_planeswalker_loyalty_dependencies(effect, *, source_ref="$source"):
    if (not isinstance(effect, Mapping) or set(effect) != {
            "op", "source", "set", "counter", "amount", PAIRED_PLANESWALKER_LOYALTY_FIELD,
        } or effect["op"] != "place_counters_on_set" or effect["source"] != source_ref
        or effect["counter"] != "+1/+1" or type(effect["amount"]) is not int or effect["amount"] != 1
        or type(effect[PAIRED_PLANESWALKER_LOYALTY_FIELD]) is not int
        or effect[PAIRED_PLANESWALKER_LOYALTY_FIELD] != 1
        or AffectedPermanentSetSpec.from_dict(effect["set"]) != controlled_counter_subjects("creature")):
        raise CounterPlacementSetError("Paired loyalty requires both fixed controlled sets and the original source")
    return ("counter.producer.fixed_permanent_set_effect", PAIRED_PLANESWALKER_LOYALTY_CAPABILITY)


class CounterPlacementSetError(ValueError):
    """A fixed affected-set counter instruction or snapshot is invalid."""


def _nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise CounterPlacementSetError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class CounterPlacementSetPermanent:
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
            _nonempty(value, field=f"Counter-set permanent {field}")

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "ref": self.ref,
            "controller": self.controller,
        }


@dataclass(frozen=True, slots=True)
class CounterPlacementSetSnapshot:
    spec: AffectedPermanentSetSpec
    permanents: tuple[CounterPlacementSetPermanent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise CounterPlacementSetError(
                "Counter-set snapshots require a typed affected set"
            )
        permanents = tuple(self.permanents)
        if any(
            not isinstance(value, CounterPlacementSetPermanent)
            for value in permanents
        ):
            raise CounterPlacementSetError(
                "Counter-set snapshots require typed permanents"
            )
        object_ids = tuple(value.object_id for value in permanents)
        logical_ids = tuple(value.logical_object_id for value in permanents)
        if len(object_ids) != len(set(object_ids)) or len(logical_ids) != len(
            set(logical_ids)
        ):
            raise CounterPlacementSetError(
                "Counter-set snapshots require unique permanent identities"
            )
        object.__setattr__(self, "permanents", permanents)

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


class CounterPlacementSetQuery(Protocol):
    def affected_permanent_active_seats(self) -> tuple[str, ...]: ...

    def affected_permanent_apnap_order(self) -> tuple[str, ...]: ...

    def affected_permanent_object_rows(
        self, actor: str
    ) -> tuple[ObjectQueryResult, ...]: ...


class CounterPlacementSetHost(
    CounterPlacementHost,
    CounterPlacementSetQuery,
    Protocol,
):
    pass


def snapshot_counter_placement_set(
    query: CounterPlacementSetQuery,
    *,
    actor: str,
    spec: AffectedPermanentSetSpec,
    source_ref: str | None = None,
) -> CounterPlacementSetSnapshot:
    """Freeze the exact public affected set before counter preflight."""

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
        raise CounterPlacementSetError(str(exc)) from exc
    return CounterPlacementSetSnapshot(
        spec=spec,
        permanents=tuple(
            CounterPlacementSetPermanent(
                object_id=row.object_id,
                logical_object_id=row.logical_object_id,
                ref=row.ref,
                controller=row.controller,
            )
            for row in selected
        ),
    )


def _set_counter_requests(snapshot, *, actor, counter_name, amount, source_ref):
    return tuple(CounterPlacementRequest(
        subject_kind="permanent", subject_id=current.object_id,
        counter_name=counter_name, amount=amount, placing_player=actor, source_ref=source_ref,
    ) for current in snapshot.permanents)


def _paired_planeswalker_requests(host, actor, source_ref, identity):
    object_id, original_incarnation = identity
    source = host.state.cards.get(object_id)
    source_is_current = (source is not None and source.zone == "battlefield"
                         and source.logical_object_id == original_incarnation)
    walkers = snapshot_counter_placement_set(
        host, actor=actor, source_ref=source_ref,
        spec=controlled_counter_subjects("planeswalker", exclude_source=source_is_current),
    )
    return _set_counter_requests(walkers, actor=actor, counter_name="loyalty", amount=1, source_ref=source_ref)


def resolve_counter_placement_set(
    host: CounterPlacementSetHost,
    *,
    actor: str,
    spec: AffectedPermanentSetSpec,
    counter_name: str,
    amount: int,
    reason: str,
    source_ref: str | None = None,
    replacement_selections: Sequence[str | dict[str, Any]] = (),
    paired_loyalty_source: tuple[str, str] | None = None,
) -> tuple[CounterPlacementResult, ...]:
    """Resolve one fixed affected set through the canonical counter owner."""

    _nonempty(actor, field="Counter-set actor")
    normalized_counter = " ".join(
        _nonempty(counter_name, field="Counter-set counter").casefold().split()
    )
    _nonempty(normalized_counter, field="Counter-set counter")
    _nonempty(reason, field="Counter-set reason")
    if type(amount) is not int or amount <= 0:
        raise CounterPlacementSetError(
            "Counter-set amount must be a positive exact integer"
        )
    if source_ref is not None:
        _nonempty(source_ref, field="Counter-set source")
    if spec.exclude_source and source_ref is None:
        raise CounterPlacementSetError(
            "Source-excluding counter sets require a source"
        )
    snapshot = snapshot_counter_placement_set(
        host,
        actor=actor,
        spec=spec,
        source_ref=source_ref,
    )
    requests = _set_counter_requests(
        snapshot, actor=actor, counter_name=normalized_counter, amount=amount, source_ref=source_ref,
    )
    if paired_loyalty_source is not None:
        requests += _paired_planeswalker_requests(host, actor, source_ref, paired_loyalty_source)
    try:
        return place_counters(host, requests, selections=replacement_selections, reason=reason)
    except CounterPlacementError as exc:
        raise CounterPlacementSetError(str(exc)) from exc


__all__ = [
    "CounterPlacementSetError",
    "CounterPlacementSetHost",
    "CounterPlacementSetPermanent",
    "CounterPlacementSetQuery",
    "CounterPlacementSetSnapshot",
    "resolve_counter_placement_set",
    "snapshot_counter_placement_set",
]
