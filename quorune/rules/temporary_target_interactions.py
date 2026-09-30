from __future__ import annotations

"""Typed predicates and choices for temporary target interactions."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import ObjectQueryResult, object_matches_query


TEMPORARY_TARGET_INTERACTION_CAPABILITY = (
    "continuous.resolution.temporary_target_interactions"
)


class TemporaryTargetInteractionError(ValueError):
    """A temporary target interaction descriptor is malformed."""


@dataclass(frozen=True, slots=True)
class TargetConditionSpec:
    """One closed disjunction over a target's current public state."""

    queries_any: tuple[ObjectQuerySpec, ...] = ()
    has_any_counter: bool = False

    def __post_init__(self) -> None:
        queries = tuple(self.queries_any)
        if (
            type(self.has_any_counter) is not bool
            or not (queries or self.has_any_counter)
            or len(queries) > 2
            or any(not isinstance(value, ObjectQuerySpec) for value in queries)
        ):
            raise TemporaryTargetInteractionError(
                "Target condition is outside the closed grammar"
            )
        for query in queries:
            if (
                query.zones != ("battlefield",)
                or query.owner is not None
                or query.controller is not None
                or query.excluded_controllers
                or query.include_phased_out
                or query.known_to_actor is not None
                or query.exclude_ref is not None
            ):
                raise TemporaryTargetInteractionError(
                    "Target condition query is outside the current public boundary"
                )
        object.__setattr__(self, "queries_any", queries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "queries_any": [query.to_dict() for query in self.queries_any],
            "has_any_counter": self.has_any_counter,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TargetConditionSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "queries_any",
            "has_any_counter",
        }:
            raise TemporaryTargetInteractionError(
                "Target condition fields are incomplete or unknown"
            )
        raw_queries = value["queries_any"]
        if not isinstance(raw_queries, (list, tuple)):
            raise TemporaryTargetInteractionError(
                "Target condition queries must be an array"
            )
        try:
            queries = tuple(
                ObjectQuerySpec.from_dict(query) for query in raw_queries
            )
        except (ObjectQueryError, TypeError, ValueError) as exc:
            raise TemporaryTargetInteractionError(str(exc)) from exc
        return cls(
            queries_any=queries,
            has_any_counter=value["has_any_counter"],
        )

    def matches(self, row: ObjectQueryResult) -> bool:
        if not isinstance(row, ObjectQueryResult):
            raise TemporaryTargetInteractionError(
                "Target condition requires current typed characteristics"
            )
        return bool(
            (self.has_any_counter and any(int(value) > 0 for value in row.counters.values()))
            or any(object_matches_query(row, query) for query in self.queries_any)
        )


__all__ = [
    "TEMPORARY_TARGET_INTERACTION_CAPABILITY",
    "TargetConditionSpec",
    "TemporaryTargetInteractionError",
]
