from __future__ import annotations

"""Closed grammar for public static spell-cost increases and reductions."""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from ..object_predicate import ObjectQuerySpec
from .cast_cost_modifier_templates import fixed_spell_predicate


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


@dataclass(frozen=True, slots=True)
class PublicCastCostModifierTemplate:
    affected_controller: CastCostAffectedController
    predicates_any: tuple[ObjectQuerySpec, ...]
    generic_adjustment: int
    cast_origin_zones: tuple[str, ...] = ()
    excluded_cast_origin_zones: tuple[str, ...] = ()
    turn_relation: CastCostTurnRelation = CastCostTurnRelation.ANY
    ordinal: CastCostOrdinal = CastCostOrdinal.ANY

    def __post_init__(self) -> None:
        if not self.predicates_any or not all(
            isinstance(value, ObjectQuerySpec) for value in self.predicates_any
        ):
            raise ValueError("Public cast-cost modifiers require typed spell predicates")
        if type(self.generic_adjustment) is not int or self.generic_adjustment == 0:
            raise ValueError("Public cast-cost modifiers require a nonzero adjustment")
        allowed_zones = {"graveyard", "exile"}
        if (
            set(self.cast_origin_zones) - allowed_zones
            or set(self.excluded_cast_origin_zones) - allowed_zones
            or self.cast_origin_zones and self.excluded_cast_origin_zones
        ):
            raise ValueError("Public cast-cost modifier origin zones are unsupported")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "affected_controller": self.affected_controller.value,
            "predicates_any": [value.to_dict() for value in self.predicates_any],
            "generic_adjustment": self.generic_adjustment,
            "cast_origin_zones": list(self.cast_origin_zones),
            "excluded_cast_origin_zones": list(self.excluded_cast_origin_zones),
            "turn_relation": self.turn_relation.value,
            "ordinal": self.ordinal.value,
        }


_ADJUSTMENT = re.compile(
    r"^(?P<subject>.+?) cost(?:s)? \{(?P<amount>[1-9][0-9]*)\} "
    r"(?P<direction>less|more) to cast\.?$",
    re.IGNORECASE,
)


def _historic_predicates() -> tuple[ObjectQuerySpec, ...]:
    return (
        ObjectQuerySpec(types_all=("artifact",)),
        ObjectQuerySpec(supertypes_all=("legendary",)),
        ObjectQuerySpec(subtypes_all=("saga",)),
    )


def _spell_predicates(subject: str) -> tuple[ObjectQuerySpec, ...] | None:
    normalized = " ".join(subject.split())
    if normalized.casefold() == "historic spells you cast":
        return _historic_predicates()
    if normalized.casefold() == "creature spells with flying you cast":
        return (
            ObjectQuerySpec(types_all=("creature",), keywords_all=("flying",)),
        )
    if normalized.casefold() == "spells with flash you cast":
        return (ObjectQuerySpec(keywords_all=("flash",)),)
    predicate = fixed_spell_predicate(normalized)
    return (predicate,) if predicate is not None else None


def _subject_spec(
    subject: str,
) -> tuple[
    CastCostAffectedController,
    tuple[ObjectQuerySpec, ...],
    tuple[str, ...],
    tuple[str, ...],
    CastCostTurnRelation,
    CastCostOrdinal,
] | None:
    normalized = " ".join(subject.split())
    turn_relation = CastCostTurnRelation.ANY
    ordinal = CastCostOrdinal.ANY
    origin_zones: tuple[str, ...] = ()
    excluded_origin_zones: tuple[str, ...] = ()

    for prefix, relation in (
        ("During turns other than yours, ", CastCostTurnRelation.NOT_SOURCE_CONTROLLER_TURN),
        ("During your turn, ", CastCostTurnRelation.SOURCE_CONTROLLER_TURN),
    ):
        if normalized.casefold().startswith(prefix.casefold()):
            normalized = normalized[len(prefix) :]
            turn_relation = relation
            break

    ordinal_match = re.fullmatch(
        r"The (?P<ordinal>first|second) (?P<quality>.+?) spell you cast each turn",
        normalized,
        re.IGNORECASE,
    )
    if ordinal_match is not None:
        ordinal = CastCostOrdinal(ordinal_match.group("ordinal").casefold())
        quality = ordinal_match.group("quality")
        normalized = (
            "Spells you cast"
            if quality.casefold() == "spell"
            else f"{quality} spells you cast"
        )

    origin_match = re.fullmatch(
        r"Spells you cast from (?P<origin>your graveyard|your graveyard or from exile)",
        normalized,
        re.IGNORECASE,
    )
    if origin_match is not None:
        origin = origin_match.group("origin").casefold()
        normalized = "Spells you cast"
        if origin == "your graveyard":
            origin_zones = ("graveyard",)
        elif origin == "your graveyard or from exile":
            origin_zones = ("graveyard", "exile")
        else:
            origin_zones = ("graveyard", "exile")

    relation = CastCostAffectedController.SOURCE_CONTROLLER
    predicate_subject = normalized
    lowered = normalized.casefold()
    if lowered in {"spell", "spells", "each spell"}:
        relation = CastCostAffectedController.ALL_PLAYERS
        predicate_subject = "Spells you cast"
    elif lowered.endswith(" spells your opponents cast"):
        relation = CastCostAffectedController.SOURCE_OPPONENTS
        quality = normalized[: -len(" spells your opponents cast")]
        predicate_subject = (
            "Spells you cast" if not quality else f"{quality} spells you cast"
        )
    elif lowered.endswith(" spells"):
        relation = CastCostAffectedController.ALL_PLAYERS
        quality = normalized[: -len(" spells")]
        predicate_subject = (
            "Spells you cast" if not quality else f"{quality} spells you cast"
        )
    predicates = _spell_predicates(predicate_subject)
    if predicates is None:
        return None
    return (
        relation,
        predicates,
        origin_zones,
        excluded_origin_zones,
        turn_relation,
        ordinal,
    )


def public_cast_cost_modifier_template(
    text: str,
) -> PublicCastCostModifierTemplate | None:
    """Parse one fixed public total-cost adjustment and reject open predicates."""

    normalized = " ".join(text.strip().split())
    match = _ADJUSTMENT.fullmatch(normalized)
    if match is None:
        return None
    subject = _subject_spec(match.group("subject"))
    if subject is None:
        return None
    (
        affected_controller,
        predicates,
        origin_zones,
        excluded_origin_zones,
        turn_relation,
        ordinal,
    ) = subject
    amount = int(match.group("amount"))
    adjustment = amount if match.group("direction").casefold() == "more" else -amount
    return PublicCastCostModifierTemplate(
        affected_controller=affected_controller,
        predicates_any=predicates,
        generic_adjustment=adjustment,
        cast_origin_zones=origin_zones,
        excluded_cast_origin_zones=excluded_origin_zones,
        turn_relation=turn_relation,
        ordinal=ordinal,
    )


__all__ = [
    "CastCostAffectedController",
    "CastCostOrdinal",
    "CastCostTurnRelation",
    "PublicCastCostModifierTemplate",
    "public_cast_cost_modifier_template",
]
