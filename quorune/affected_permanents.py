from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any, Iterable, Mapping

from .object_predicate import ObjectQueryError, ObjectQuerySpec
from .object_query import ObjectQueryResult, query_objects
from .util import stable_json
from .mana_value_filter import ManaValueFilter


class AffectedPermanentSetError(ValueError):
    """A public battlefield-set descriptor or snapshot is invalid."""


class PermanentControllerRelation(str, Enum):
    ANY = "any"
    ACTOR = "actor"
    OPPONENTS = "opponents"
    TARGET_PLAYER = "target_player"


_SET_FIELDS = frozenset(
    {"controller_relation", "target_controller", "exclude_source", "query"}
)


def _nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise AffectedPermanentSetError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class AffectedPermanentSetSpec:
    """Closed immutable description of one public battlefield object set."""

    query: ObjectQuerySpec
    controller_relation: PermanentControllerRelation = (
        PermanentControllerRelation.ANY
    )
    target_controller: str | None = None
    exclude_source: bool = False
    mana_value_filter: ManaValueFilter | None = None

    def __post_init__(self) -> None:
        if self.mana_value_filter is not None and not isinstance(self.mana_value_filter,ManaValueFilter):
            raise AffectedPermanentSetError('Affected permanent mana-value filters must be typed')
        if not isinstance(self.query, ObjectQuerySpec):
            raise AffectedPermanentSetError(
                "Affected permanent sets require a typed object query"
            )
        if not isinstance(self.controller_relation, PermanentControllerRelation):
            raise AffectedPermanentSetError(
                "Affected permanent controller relation is unsupported"
            )
        if self.query.zones != ("battlefield",):
            raise AffectedPermanentSetError(
                "Affected permanent sets are battlefield-only"
            )
        if self.query.owner is not None or self.query.controller is not None:
            raise AffectedPermanentSetError(
                "Affected permanent sets use a typed controller relation"
            )
        if self.query.include_phased_out:
            raise AffectedPermanentSetError(
                "Affected permanent sets exclude phased-out objects"
            )
        if self.query.known_to_actor is not None:
            raise AffectedPermanentSetError(
                "Public battlefield sets do not use knowledge predicates"
            )
        if self.query.exclude_ref is not None:
            raise AffectedPermanentSetError(
                "Affected permanent sets use the typed source exclusion"
            )
        if type(self.exclude_source) is not bool:
            raise AffectedPermanentSetError(
                "Affected permanent source exclusion must be boolean"
            )
        if self.controller_relation is PermanentControllerRelation.TARGET_PLAYER:
            _nonempty(
                self.target_controller,
                field="Affected permanent target controller",
            )
        elif self.target_controller is not None:
            raise AffectedPermanentSetError(
                "Only target-player sets accept a target controller"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "controller_relation": self.controller_relation.value,
            "target_controller": self.target_controller,
            "exclude_source": self.exclude_source,
            "query": self.query.canonical_dict(),
            **({'mana_value_filter':self.mana_value_filter.to_dict()} if self.mana_value_filter is not None else {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AffectedPermanentSetSpec":
        if not isinstance(value, Mapping) or frozenset(value) != _SET_FIELDS | ({'mana_value_filter'} if 'mana_value_filter' in value else set()):
            raise AffectedPermanentSetError(
                "Affected permanent set fields are incomplete or unknown"
            )
        try:
            return cls(
                query=ObjectQuerySpec.from_dict(value["query"]),
                controller_relation=PermanentControllerRelation(
                    value["controller_relation"]
                ),
                target_controller=value["target_controller"],
                exclude_source=value["exclude_source"],
                mana_value_filter=ManaValueFilter.from_dict(value['mana_value_filter']) if 'mana_value_filter' in value else None,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ObjectQueryError,
        ) as exc:
            if isinstance(exc, AffectedPermanentSetError):
                raise
            raise AffectedPermanentSetError(
                "Affected permanent set descriptor is malformed"
            ) from exc

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


def select_affected_permanents(
    rows: Iterable[ObjectQueryResult],
    spec: AffectedPermanentSetSpec,
    *,
    actor: str,
    active_seats: Iterable[str],
    apnap_order: Iterable[str],
    source_ref: str | None = None,
) -> tuple[ObjectQueryResult, ...]:
    """Select and canonically APNAP-order one immutable public set."""

    if not isinstance(spec, AffectedPermanentSetSpec):
        raise AffectedPermanentSetError(
            "Affected permanent selection requires a typed set"
        )
    _nonempty(actor, field="Affected permanent actor")
    active = tuple(active_seats)
    order = tuple(apnap_order)
    if (
        actor not in active
        or len(active) != len(set(active))
        or len(order) != len(active)
        or set(order) != set(active)
    ):
        raise AffectedPermanentSetError(
            "Affected permanent selection requires a complete APNAP view"
        )
    if spec.exclude_source:
        _nonempty(source_ref, field="Affected permanent source")
    order_index = {seat: index for index, seat in enumerate(order)}
    if spec.mana_value_filter is not None:
        try:spec.mana_value_filter.resolved_value
        except ValueError as exc:raise AffectedPermanentSetError(str(exc)) from exc
    selected = query_objects(tuple(rows), spec.query)
    if spec.mana_value_filter is not None:
        selected=tuple(row for row in selected if spec.mana_value_filter.matches(row.mana_value))
    if spec.controller_relation is PermanentControllerRelation.ACTOR:
        selected = tuple(row for row in selected if row.controller == actor)
    elif spec.controller_relation is PermanentControllerRelation.OPPONENTS:
        selected = tuple(row for row in selected if row.controller != actor)
    elif spec.controller_relation is PermanentControllerRelation.TARGET_PLAYER:
        if spec.target_controller not in active:
            raise AffectedPermanentSetError(
                "Affected permanent target controller is no longer active"
            )
        selected = tuple(
            row for row in selected if row.controller == spec.target_controller
        )
    if spec.exclude_source:
        selected = tuple(row for row in selected if row.ref != source_ref)
    if any(
        row.controller not in order_index
        or not row.ref
        or not row.object_id
        or not row.logical_object_id
        for row in selected
    ):
        raise AffectedPermanentSetError(
            "Affected permanent query returned an invalid public identity"
        )
    deduplicated: dict[str, ObjectQueryResult] = {}
    for row in selected:
        previous = deduplicated.get(row.logical_object_id)
        if previous is not None and previous.object_id != row.object_id:
            raise AffectedPermanentSetError(
                "Affected permanent query repeated one logical object"
            )
        deduplicated[row.logical_object_id] = row
    return tuple(
        sorted(
            deduplicated.values(),
            key=lambda row: (
                order_index[row.controller],
                row.logical_object_id,
                row.object_id,
                row.ref,
            ),
        )
    )


__all__ = [
    "AffectedPermanentSetError",
    "AffectedPermanentSetSpec",
    "PermanentControllerRelation",
    "select_affected_permanents",
]
