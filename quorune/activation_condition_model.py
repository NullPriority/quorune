from __future__ import annotations

"""Typed public predicates and exact Oracle tails for activated abilities."""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping

from .activation_usage import ActivationLimit
from .creature_subtypes import canonical_creature_subtype
from .object_predicate import ObjectQueryError, ObjectQuerySpec


ACTIVATION_PHASE_CONDITION_CAPABILITY = "activation.condition.phase_window"
ACTIVATION_PUBLIC_QUERY_CAPABILITY = "activation.condition.public_query"


class ActivationConditionKind(str, Enum):
    CONTROLLERS_TURN = "controllers_turn"
    CONTROLLERS_UPKEEP = "controllers_upkeep"
    CONTROLLERS_TURN_BEFORE_ATTACKERS = "controllers_turn_before_attackers"
    NOT_CONTROLLERS_TURN = "not_controllers_turn"
    TOKEN_CREATED_THIS_TURN = "token_created_this_turn"
    CONTROLS_TYPE = "controls_type"
    GRAVEYARD_DISTINCT_TYPES = "graveyard_distinct_types"
    PUBLIC_QUERY_COUNT = "public_query_count"
    UNSUPPORTED = "unsupported"


_PUBLIC_ZONES = frozenset({"battlefield", "graveyard", "hand"})


def _closed_public_query(query: ObjectQuerySpec) -> bool:
    if (
        len(query.zones) != 1
        or query.zones[0] not in _PUBLIC_ZONES
        or query.owner is not None
        or query.controller is not None
        or query.excluded_controllers
        or query.excluded_types
        or query.subtypes_any
        or query.excluded_subtypes
        or query.colors_any
        or query.colorless is not None
        or query.minimum_color_count is not None
        or query.token is not None
        or query.tapped is not None
        or query.include_phased_out
        or query.known_to_actor is not None
        or query.exclude_ref is not None
        or query.state_predicate is not None
    ):
        return False
    if query.zones == ("hand",):
        return query == ObjectQuerySpec(zones=("hand",))
    if query.zones == ("graveyard",) and any(
        (
            query.types_any,
            query.supertypes_all,
            query.colors_all,
            query.keywords_all,
        )
    ):
        return False
    return True


@dataclass(frozen=True, slots=True)
class ActivationCondition:
    """Closed compiler-pinned predicate evaluated before activation."""

    kind: ActivationConditionKind
    minimum: int | None = None
    card_type: str | None = None
    maximum: int | None = None
    query: ObjectQuerySpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActivationConditionKind):
            try:
                object.__setattr__(self, "kind", ActivationConditionKind(self.kind))
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported activation condition kind") from exc
        if self.card_type is not None and (
            not isinstance(self.card_type, str) or not self.card_type.strip()
        ):
            raise ValueError("activation condition card_type must be nonempty")
        if self.card_type is not None:
            object.__setattr__(self, "card_type", self.card_type.casefold().strip())
        if self.kind is ActivationConditionKind.PUBLIC_QUERY_COUNT:
            if (
                type(self.minimum) is not int
                or self.minimum < 0
                or (
                    self.maximum is not None
                    and (
                        type(self.maximum) is not int
                        or self.maximum < self.minimum
                    )
                )
                or self.card_type is not None
                or not isinstance(self.query, ObjectQuerySpec)
                or not _closed_public_query(self.query)
            ):
                raise ValueError(
                    "public-query activation conditions require a closed count range"
                )
            return
        if self.minimum is not None and (
            type(self.minimum) is not int or self.minimum < 1
        ):
            raise ValueError("activation condition minimum must be positive")
        if self.maximum is not None or self.query is not None:
            raise ValueError(
                "only public-query activation conditions accept query ranges"
            )
        if self.kind is ActivationConditionKind.CONTROLS_TYPE:
            if self.minimum is None or self.card_type not in {
                "artifact",
                "creature",
                "land",
            }:
                raise ValueError(
                    "controls-type conditions require a supported type and minimum"
                )
        elif self.kind is ActivationConditionKind.GRAVEYARD_DISTINCT_TYPES:
            if self.minimum is None or self.card_type is not None:
                raise ValueError(
                    "graveyard-type conditions require only a minimum"
                )
        elif self.minimum is not None or self.card_type is not None:
            raise ValueError("this activation condition takes no parameters")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "kind": self.kind.value,
            "minimum": self.minimum,
            "card_type": self.card_type,
        }
        if self.kind is ActivationConditionKind.PUBLIC_QUERY_COUNT:
            assert self.query is not None
            value.update(
                {
                    "maximum": self.maximum,
                    "query": self.query.to_dict(),
                }
            )
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActivationCondition":
        fields = frozenset(value) if isinstance(value, Mapping) else frozenset()
        if fields not in {
            frozenset({"kind", "minimum", "card_type"}),
            frozenset({"kind", "minimum", "card_type", "maximum", "query"}),
        }:
            raise ValueError("activation conditions use a closed schema")
        try:
            query = (
                ObjectQuerySpec.from_dict(value["query"])
                if "query" in value
                else None
            )
        except (ObjectQueryError, TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        return cls(
            kind=value["kind"],
            minimum=value["minimum"],
            card_type=value["card_type"],
            maximum=value.get("maximum"),
            query=query,
        )


@dataclass(frozen=True, slots=True)
class ActivationRestrictionSpec:
    """One exact trailing activation restriction and its typed metadata."""

    sorcery_speed: bool = False
    activation_limit: ActivationLimit | None = None
    conditions: tuple[ActivationCondition, ...] = ()


_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_NUMBER = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*)"
_PERMANENT_TYPES = (
    "artifact",
    "battle",
    "creature",
    "enchantment",
    "land",
    "planeswalker",
)
_COLOR_SYMBOLS = {
    "black": "B",
    "blue": "U",
    "green": "G",
    "red": "R",
    "white": "W",
}
_CONTROL_QUERY = re.compile(
    rf"^if you control (?:(?P<count>{_NUMBER}) or more |"
    r"(?P<article>a|an) |(?P<none>no) )(?P<object>[a-z][a-z' -]+)$",
    re.IGNORECASE,
)
_GRAVEYARD_QUERY = re.compile(
    rf"^if there (?:are|is) (?:(?P<count>{_NUMBER}) or more |an? )"
    r"(?P<object>(?:[a-z][a-z' -]* )?cards?) in your graveyard$",
    re.IGNORECASE,
)
_HAND_QUERY = re.compile(
    rf"^if you have (?:(?P<none>no)|exactly (?P<exact>{_NUMBER})|"
    rf"(?P<count>{_NUMBER}) or (?P<bound>more|fewer)) cards? in "
    r"(?:your )?hand$",
    re.IGNORECASE,
)


def _number(value: str) -> int:
    normalized = value.casefold()
    return int(normalized) if normalized.isdigit() else _NUMBER_WORDS[normalized]


def _permanent_query(descriptor: str) -> ObjectQuerySpec | None:
    normalized = " ".join(descriptor.casefold().split())
    singular = {
        "artifacts": "artifact",
        "battles": "battle",
        "creatures": "creature",
        "enchantments": "enchantment",
        "lands": "land",
        "permanents": "permanent",
        "planeswalkers": "planeswalker",
    }.get(normalized, normalized)
    if singular.endswith(" permanents"):
        singular = singular.removesuffix("s")
    fields: dict[str, Any] = {"zones": ("battlefield",)}
    if singular in {"artifact", "creature", "land"}:
        fields["types_all"] = (singular,)
    elif singular == "permanent":
        fields["types_any"] = _PERMANENT_TYPES
    elif singular == "legendary creature":
        fields["types_all"] = ("creature",)
        fields["supertypes_all"] = ("legendary",)
    elif singular == "creature with flying":
        fields["types_all"] = ("creature",)
        fields["keywords_all"] = ("flying",)
    elif singular == "snow permanent":
        fields["types_any"] = _PERMANENT_TYPES
        fields["supertypes_all"] = ("snow",)
    elif singular.endswith(" permanent"):
        color = singular.removesuffix(" permanent")
        if color not in _COLOR_SYMBOLS:
            return None
        fields["types_any"] = _PERMANENT_TYPES
        fields["colors_all"] = (_COLOR_SYMBOLS[color],)
    elif singular.endswith(" planeswalker"):
        subtype = singular.removesuffix(" planeswalker")
        if re.fullmatch(r"[a-z][a-z'-]*", subtype) is None:
            return None
        fields["types_all"] = ("planeswalker",)
        fields["subtypes_all"] = (subtype,)
    else:
        subtype = canonical_creature_subtype(singular)
        if subtype is None:
            return None
        fields["types_all"] = ("creature",)
        fields["subtypes_all"] = (subtype,)
    return ObjectQuerySpec(**fields)


def _public_query_condition(text: str) -> ActivationCondition | None:
    hand = _HAND_QUERY.fullmatch(text)
    if hand is not None:
        if hand.group("none") is not None:
            minimum = maximum = 0
        elif hand.group("exact") is not None:
            minimum = maximum = _number(hand.group("exact"))
        else:
            amount = _number(hand.group("count"))
            at_most = hand.group("bound").casefold() == "fewer"
            minimum = 0 if at_most else amount
            maximum = amount if at_most else None
        return ActivationCondition(
            ActivationConditionKind.PUBLIC_QUERY_COUNT,
            minimum=minimum,
            maximum=maximum,
            query=ObjectQuerySpec(zones=("hand",)),
        )
    graveyard = _GRAVEYARD_QUERY.fullmatch(text)
    if graveyard is not None:
        raw_object = " ".join(graveyard.group("object").casefold().split())
        descriptor = (
            ""
            if raw_object in {"card", "cards"}
            else re.sub(r"\s+cards?$", "", raw_object).strip()
        )
        fields: dict[str, Any] = {"zones": ("graveyard",)}
        if descriptor:
            card_type = descriptor.removesuffix("s")
            if card_type in _PERMANENT_TYPES or card_type in {"instant", "sorcery"}:
                fields["types_all"] = (card_type,)
            else:
                subtype = canonical_creature_subtype(card_type)
                if subtype is None:
                    return None
                fields["types_all"] = ("creature",)
                fields["subtypes_all"] = (subtype,)
        return ActivationCondition(
            ActivationConditionKind.PUBLIC_QUERY_COUNT,
            minimum=(
                _number(graveyard.group("count"))
                if graveyard.group("count") is not None
                else 1
            ),
            query=ObjectQuerySpec(**fields),
        )
    controlled = _CONTROL_QUERY.fullmatch(text)
    if controlled is None:
        return None
    query = _permanent_query(controlled.group("object"))
    if query is None:
        return None
    if controlled.group("none") is not None:
        minimum = maximum = 0
    else:
        minimum = (
            _number(controlled.group("count"))
            if controlled.group("count") is not None
            else 1
        )
        maximum = None
    if (
        maximum is None
        and query.types_all in {("artifact",), ("creature",), ("land",)}
        and not any(
            (
                query.subtypes_all,
                query.supertypes_all,
                query.colors_all,
                query.keywords_all,
            )
        )
    ):
        return ActivationCondition(
            ActivationConditionKind.CONTROLS_TYPE,
            minimum=minimum,
            card_type=query.types_all[0],
        )
    return ActivationCondition(
        ActivationConditionKind.PUBLIC_QUERY_COUNT,
        minimum=minimum,
        maximum=maximum,
        query=query,
    )


def activation_restriction_spec(text: str) -> ActivationRestrictionSpec | None:
    """Parse one complete activation restriction without interpreting an effect."""

    normalized = " ".join(text.casefold().rstrip(".").split())
    fixed = {
        "as a sorcery": ActivationRestrictionSpec(sorcery_speed=True),
        "as a sorcery and only once each turn": ActivationRestrictionSpec(
            sorcery_speed=True,
            activation_limit=ActivationLimit.ONCE_PER_TURN,
        ),
        "during your turn": ActivationRestrictionSpec(
            conditions=(ActivationCondition(ActivationConditionKind.CONTROLLERS_TURN),)
        ),
        "during your turn and only once each turn": ActivationRestrictionSpec(
            activation_limit=ActivationLimit.ONCE_PER_TURN,
            conditions=(ActivationCondition(ActivationConditionKind.CONTROLLERS_TURN),),
        ),
        "during your upkeep": ActivationRestrictionSpec(
            conditions=(
                ActivationCondition(ActivationConditionKind.CONTROLLERS_UPKEEP),
            )
        ),
        "during your upkeep and only once each turn": ActivationRestrictionSpec(
            activation_limit=ActivationLimit.ONCE_PER_TURN,
            conditions=(
                ActivationCondition(ActivationConditionKind.CONTROLLERS_UPKEEP),
            ),
        ),
        "during your turn, before attackers are declared": ActivationRestrictionSpec(
            conditions=(
                ActivationCondition(
                    ActivationConditionKind.CONTROLLERS_TURN_BEFORE_ATTACKERS
                ),
            )
        ),
        "once each turn": ActivationRestrictionSpec(
            activation_limit=ActivationLimit.ONCE_PER_TURN,
        ),
        "if it's not your turn": ActivationRestrictionSpec(
            conditions=(
                ActivationCondition(ActivationConditionKind.NOT_CONTROLLERS_TURN),
            )
        ),
        "if you created a token this turn": ActivationRestrictionSpec(
            conditions=(
                ActivationCondition(ActivationConditionKind.TOKEN_CREATED_THIS_TURN),
            )
        ),
        (
            "if there are four or more card types among cards in your graveyard"
        ): ActivationRestrictionSpec(
            conditions=(
                ActivationCondition(
                    ActivationConditionKind.GRAVEYARD_DISTINCT_TYPES,
                    minimum=4,
                ),
            )
        ),
    }
    if normalized in fixed:
        return fixed[normalized]
    query = _public_query_condition(normalized)
    return (
        ActivationRestrictionSpec(conditions=(query,))
        if query is not None
        else None
    )


__all__ = [
    "ACTIVATION_PHASE_CONDITION_CAPABILITY",
    "ACTIVATION_PUBLIC_QUERY_CAPABILITY",
    "ActivationCondition",
    "ActivationConditionKind",
    "ActivationRestrictionSpec",
    "activation_restriction_spec",
]
