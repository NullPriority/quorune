from __future__ import annotations

"""Typed public fixed-generic spell-cost modifiers."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .object_predicate import ObjectQueryError, ObjectQuerySpec


class CastCostModifierError(ValueError):
    """A public spell-cost modifier descriptor is malformed."""


class CastCostAffectedController(str, Enum):
    SOURCE_CONTROLLER = "source_controller"
    SOURCE_OPPONENTS = "source_opponents"
    ALL_PLAYERS = "all_players"


class CastCostTurnRelation(str, Enum):
    ANY = "any"
    SOURCE_CONTROLLER_TURN = "source_controller_turn"
    NOT_SOURCE_CONTROLLER_TURN = "not_source_controller_turn"


class CastCostOrdinal(str, Enum):
    ANY = "any"
    FIRST = "first"
    SECOND = "second"


_PUBLIC_ORIGIN_ZONES = frozenset({"graveyard", "exile"})


@dataclass(frozen=True, slots=True)
class PublicCastCostModifierSpec:
    affected_controller: CastCostAffectedController
    predicates_any: tuple[ObjectQuerySpec, ...]
    generic_adjustment: int
    cast_origin_zones: tuple[str, ...] = ()
    excluded_cast_origin_zones: tuple[str, ...] = ()
    turn_relation: CastCostTurnRelation = CastCostTurnRelation.ANY
    ordinal: CastCostOrdinal = CastCostOrdinal.ANY
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CastCostModifierError(
                "Unsupported public cast-cost modifier schema version"
            )
        if not isinstance(self.affected_controller, CastCostAffectedController):
            raise CastCostModifierError(
                "Public cast-cost modifier controller relation is unsupported"
            )
        predicates = tuple(self.predicates_any)
        if not predicates or any(
            not isinstance(value, ObjectQuerySpec) for value in predicates
        ):
            raise CastCostModifierError(
                "Public cast-cost modifiers require typed spell predicates"
            )
        for predicate in predicates:
            if (
                predicate.zones
                or predicate.owner is not None
                or predicate.controller is not None
                or predicate.token is not None
                or predicate.tapped is not None
                or predicate.include_phased_out
                or predicate.known_to_actor is not None
                or predicate.exclude_ref is not None
                or predicate.state_predicate is not None
            ):
                raise CastCostModifierError(
                    "Public cast-cost modifiers require public spell characteristics"
                )
        object.__setattr__(self, "predicates_any", predicates)
        if type(self.generic_adjustment) is not int or not self.generic_adjustment:
            raise CastCostModifierError(
                "Public cast-cost modifiers require a nonzero generic adjustment"
            )
        origins = tuple(str(value) for value in self.cast_origin_zones)
        excluded = tuple(str(value) for value in self.excluded_cast_origin_zones)
        if (
            not set(origins) <= _PUBLIC_ORIGIN_ZONES
            or not set(excluded) <= _PUBLIC_ORIGIN_ZONES
            or len(origins) != len(set(origins))
            or len(excluded) != len(set(excluded))
            or (origins and excluded)
        ):
            raise CastCostModifierError(
                "Public cast-cost modifier origin zones are unsupported"
            )
        object.__setattr__(self, "cast_origin_zones", origins)
        object.__setattr__(self, "excluded_cast_origin_zones", excluded)
        if not isinstance(self.turn_relation, CastCostTurnRelation):
            raise CastCostModifierError(
                "Public cast-cost modifier turn relation is unsupported"
            )
        if not isinstance(self.ordinal, CastCostOrdinal):
            raise CastCostModifierError(
                "Public cast-cost modifier ordinal is unsupported"
            )
        if self.ordinal is not CastCostOrdinal.ANY and any(
            predicate
            != ObjectQuerySpec(
                types_all=predicate.types_all,
                types_any=predicate.types_any,
                excluded_types=predicate.excluded_types,
            )
            for predicate in predicates
        ):
            raise CastCostModifierError(
                "Ordinal cast modifiers require turn-journaled spell types"
            )

    @property
    def predicate(self) -> ObjectQuerySpec:
        """Compatibility view for the original single-predicate shape."""

        return self.predicates_any[0]

    @property
    def generic_reduction(self) -> int:
        """Compatibility view for the original reduction-only shape."""

        return max(0, -self.generic_adjustment)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "affected_controller": self.affected_controller.value,
            "predicates_any": [value.to_dict() for value in self.predicates_any],
            "generic_adjustment": self.generic_adjustment,
            "cast_origin_zones": list(self.cast_origin_zones),
            "excluded_cast_origin_zones": list(self.excluded_cast_origin_zones),
            "turn_relation": self.turn_relation.value,
            "ordinal": self.ordinal.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicCastCostModifierSpec":
        expected = {
            "schema_version",
            "affected_controller",
            "predicates_any",
            "generic_adjustment",
            "cast_origin_zones",
            "excluded_cast_origin_zones",
            "turn_relation",
            "ordinal",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise CastCostModifierError(
                "Public cast-cost modifier descriptors have a closed schema"
            )
        raw_predicates = value["predicates_any"]
        if not isinstance(raw_predicates, list):
            raise CastCostModifierError(
                "Public cast-cost modifier predicates must be an array"
            )
        try:
            return cls(
                affected_controller=CastCostAffectedController(
                    value["affected_controller"]
                ),
                predicates_any=tuple(
                    ObjectQuerySpec.from_dict(predicate)
                    for predicate in raw_predicates
                ),
                generic_adjustment=value["generic_adjustment"],
                cast_origin_zones=tuple(value["cast_origin_zones"]),
                excluded_cast_origin_zones=tuple(
                    value["excluded_cast_origin_zones"]
                ),
                turn_relation=CastCostTurnRelation(value["turn_relation"]),
                ordinal=CastCostOrdinal(value["ordinal"]),
                schema_version=value["schema_version"],
            )
        except (ObjectQueryError, TypeError, ValueError) as exc:
            raise CastCostModifierError(str(exc)) from exc


__all__ = [
    "CastCostAffectedController",
    "CastCostModifierError",
    "CastCostOrdinal",
    "CastCostTurnRelation",
    "PublicCastCostModifierSpec",
]
