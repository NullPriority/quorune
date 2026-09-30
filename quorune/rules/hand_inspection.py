from __future__ import annotations

"""Typed predicates for bounded target-hand inspection effects."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import ObjectQueryResult, object_matches_query


FIXED_HAND_INSPECTION_OPERATION = "fixed_hand_inspection"


class HandInspectionMode(str, Enum):
    REVEAL = "reveal"
    LOOK = "look"


class HandInspectionAction(str, Enum):
    OBSERVE = "observe"
    DISCARD = "discard"
    EXILE = "exile"
    DISCARD_ALL = "discard_all"


class HandInspectionError(ValueError):
    """A target-hand inspection descriptor is malformed."""


@dataclass(frozen=True, slots=True)
class InspectHandIntent:
    actor: str
    player: str
    refs: tuple[str, ...]
    reason: str
    public: bool

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Hand-inspection intents require actor, player, and reason"
            )
        refs = tuple(self.refs)
        if (
            any(type(value) is not str or not value for value in refs)
            or len(refs) != len(set(refs))
            or type(self.public) is not bool
        ):
            raise ValueError("Hand-inspection intent is malformed")
        object.__setattr__(self, "refs", refs)


@dataclass(frozen=True, slots=True)
class HandCardPredicateSpec:
    """One closed current-characteristic predicate over cards in a hand."""

    query: ObjectQuerySpec
    excluded_supertypes: tuple[str, ...] = ()
    mana_value_min: int | None = None
    mana_value_max: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, ObjectQuerySpec):
            raise HandInspectionError(
                "Hand-card predicates require a typed object query"
            )
        query = self.query
        if (
            query.zones != ("hand",)
            or query.owner is not None
            or query.controller is not None
            or query.excluded_controllers
            or query.subtypes_all
            or query.excluded_subtypes
            or query.colors_all
            or query.colorless is not None
            or query.minimum_color_count is not None
            or query.keywords_all
            or query.keywords_none
            or query.token is not None
            or query.tapped is not None
            or query.include_phased_out
            or query.known_to_actor is not None
            or query.exclude_ref is not None
            or query.state_predicate is not None
        ):
            raise HandInspectionError(
                "Hand-card predicate query is outside the closed grammar"
            )
        excluded = tuple(
            sorted(
                str(value).casefold()
                for value in self.excluded_supertypes
                if str(value)
            )
        )
        if (
            len(excluded) != len(self.excluded_supertypes)
            or len(excluded) != len(set(excluded))
            or any(value not in {"basic", "legendary"} for value in excluded)
        ):
            raise HandInspectionError(
                "Hand-card excluded supertypes are unsupported"
            )
        values = (self.mana_value_min, self.mana_value_max)
        if (
            sum(value is not None for value in values) > 1
            or any(
                value is not None and (type(value) is not int or value < 0)
                for value in values
            )
        ):
            raise HandInspectionError(
                "Hand-card mana-value predicate is unsupported"
            )
        object.__setattr__(self, "excluded_supertypes", excluded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.to_dict(),
            "excluded_supertypes": list(self.excluded_supertypes),
            "mana_value_min": self.mana_value_min,
            "mana_value_max": self.mana_value_max,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HandCardPredicateSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "query",
            "excluded_supertypes",
            "mana_value_min",
            "mana_value_max",
        }:
            raise HandInspectionError(
                "Hand-card predicate fields are incomplete or unknown"
            )
        try:
            query = ObjectQuerySpec.from_dict(value["query"])
        except (ObjectQueryError, TypeError, ValueError) as exc:
            raise HandInspectionError(str(exc)) from exc
        excluded = value["excluded_supertypes"]
        if not isinstance(excluded, (list, tuple)):
            raise HandInspectionError(
                "Hand-card excluded supertypes must be an array"
            )
        return cls(
            query=query,
            excluded_supertypes=tuple(excluded),
            mana_value_min=value["mana_value_min"],
            mana_value_max=value["mana_value_max"],
        )

    def matches(self, row: ObjectQueryResult) -> bool:
        if not isinstance(row, ObjectQueryResult):
            raise HandInspectionError(
                "Hand-card matching requires current typed characteristics"
            )
        if not object_matches_query(row, self.query):
            return False
        if not set(self.excluded_supertypes).isdisjoint(row.supertypes):
            return False
        if (
            self.mana_value_min is not None
            and row.mana_value < self.mana_value_min
        ):
            return False
        if (
            self.mana_value_max is not None
            and row.mana_value > self.mana_value_max
        ):
            return False
        return True


__all__ = [
    "FIXED_HAND_INSPECTION_OPERATION",
    "HandCardPredicateSpec",
    "HandInspectionAction",
    "HandInspectionError",
    "HandInspectionMode",
    "InspectHandIntent",
]
