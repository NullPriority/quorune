from __future__ import annotations

"""Closed Oracle grammar for reusable spell additional costs."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..additional_cost_vocabulary import (
    ALTERNATIVE_ADDITIONAL_COST_KIND,
    DISCARD_ONE_COST,
    EXILE_ONE_FROM_BATTLEFIELD_COST,
    EXILE_ONE_FROM_GRAVEYARD_COST,
    FIXED_ZONE_CHANGE_COST_CONTRACTS,
    FIXED_LIFE_PAYMENT_COST_KIND,
    FIXED_MANA_PAYMENT_COST_KIND,
    RETURN_ONE_TO_OWNER_HAND_COST,
    SACRIFICE_COST_KIND,
    SACRIFICE_ONE_COST,
    ZONE_CHANGE_COST_KIND,
)
from ..object_predicate import ObjectQuerySpec
from ..util import mana_cost_to_vector
from .creature_subtypes import canonical_creature_subtype
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


_COUNTER_NAME = (
    r"[+-]\d+/[+-]\d+|"
    r"[A-Za-z][A-Za-z'-]*(?: [A-Za-z][A-Za-z'-]*){0,2}"
)
_FIXED_COUNTER_COST = re.compile(
    rf"As an additional cost to cast this spell, put "
    rf"(?P<count>{FIXED_COUNT_PATTERN}) (?P<counter>{_COUNTER_NAME}) "
    r"(?P<plural>counter|counters) on a creature you control\.?",
    re.IGNORECASE,
)
_PERMANENT_TYPE_PATTERN = (
    r"artifact|battle|creature|enchantment|land|planeswalker"
)
_FIXED_SACRIFICE_COST = re.compile(
    rf"As an additional cost to cast this spell, sacrifice "
    rf"(?P<article>a|an) "
    rf"(?P<first>{_PERMANENT_TYPE_PATTERN}|permanent)"
    rf"(?: or (?P<second>{_PERMANENT_TYPE_PATTERN}))?\.?",
    re.IGNORECASE,
)
_QUALIFIED_SACRIFICE_COST = re.compile(
    r"As an additional cost to cast this spell, sacrifice "
    r"(?P<article>a|an) (?P<quality>[A-Za-z][A-Za-z -]*)\.?",
    re.IGNORECASE,
)
_FIXED_DISCARD_COST = re.compile(
    r"As an additional cost to cast this spell, discard "
    r"(?P<article>a|an) (?:(?P<quality>[A-Za-z]+(?: or [A-Za-z]+)?) )?card\.?",
    re.IGNORECASE,
)
_FIXED_GRAVEYARD_EXILE_COST = re.compile(
    r"As an additional cost to cast this spell, exile "
    r"(?P<article>a|an) (?:(?P<quality>[A-Za-z]+(?: or [A-Za-z]+)?) )?card "
    r"from your graveyard\.?",
    re.IGNORECASE,
)
_FIXED_BATTLEFIELD_EXILE_COST = re.compile(
    r"As an additional cost to cast this spell, exile "
    r"(?P<article>a|an) (?P<quality>[A-Za-z ]+) you control\.?",
    re.IGNORECASE,
)
_FIXED_RETURN_COST = re.compile(
    r"As an additional cost to cast this spell, return "
    r"(?P<article>a|an) (?P<quality>[A-Za-z ]+) you control "
    r"to its owner's hand\.?",
    re.IGNORECASE,
)
_FIXED_LIFE_COST = re.compile(
    rf"As an additional cost to cast this spell, pay "
    rf"(?P<count>{FIXED_COUNT_PATTERN}|\d+) life\.?",
    re.IGNORECASE,
)
_FIXED_MANA_LEAF = re.compile(
    r"pay (?P<mana>(?:\{(?:\d+|[WUBRGC])\})+)",
    re.IGNORECASE,
)
_ADDITIONAL_COST_CLAUSE = re.compile(
    r"As an additional cost to cast this spell, (?P<body>.+?)\.?",
    re.IGNORECASE,
)
_COLOR_WORDS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
    "colorless": "C",
}
_PERMANENT_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)
_FIXED_NONCREATURE_SUBTYPES = frozenset(
    {
        "aura",
        "clue",
        "desert",
        "food",
        "forest",
        "island",
        "mountain",
        "plains",
        "room",
        "swamp",
        "treasure",
    }
)


def _creature_you_control_query() -> ObjectQuerySpec:
    return ObjectQuerySpec(
        zones=("battlefield",),
        controller="$actor",
        types_all=("creature",),
        known_to_actor=True,
    )


def _permanent_you_control_query(
    types_any: tuple[str, ...],
) -> ObjectQuerySpec:
    return ObjectQuerySpec(
        zones=("battlefield",),
        controller="$actor",
        types_any=types_any,
        known_to_actor=True,
    )


@dataclass(frozen=True, slots=True)
class FixedCounterAdditionalCostTemplate:
    """One mandatory fixed counter placement paid while casting a spell."""

    amount: int
    counter_name: str

    def __post_init__(self) -> None:
        normalized = " ".join(self.counter_name.casefold().split())
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError("Counter additional-cost amount must be positive")
        if not normalized or re.fullmatch(_COUNTER_NAME, normalized) is None:
            raise ValueError("Counter additional-cost name is unsupported")
        object.__setattr__(self, "counter_name", normalized)

    @property
    def template_id(self) -> str:
        return "spell-additional-cost-fixed-counter-creature-you-control-v1"

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "kind": "counter_placement",
            "counter": self.counter_name,
            "amount": self.amount,
            "choice_field": "counter_cost_card",
            "predicate": _creature_you_control_query().to_dict(),
        }

    @property
    def cost_schema(self) -> Mapping[str, Any]:
        return {"additional_costs": [dict(self.descriptor)]}


@dataclass(frozen=True, slots=True)
class FixedSacrificeAdditionalCostTemplate:
    """One mandatory sacrifice of a controlled permanent while casting."""

    permanent_types: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized = tuple(
            sorted(str(value).casefold() for value in self.permanent_types)
        )
        allowed = {
            "artifact",
            "battle",
            "creature",
            "enchantment",
            "land",
            "planeswalker",
        }
        if (
            len(normalized) > 2
            or len(normalized) != len(set(normalized))
            or not set(normalized).issubset(allowed)
        ):
            raise ValueError(
                "Sacrifice additional-cost types are outside the closed family"
            )
        object.__setattr__(self, "permanent_types", normalized)

    @property
    def template_id(self) -> str:
        suffix = "permanent" if not self.permanent_types else "-or-".join(
            self.permanent_types
        )
        return f"spell-additional-cost-fixed-sacrifice-{suffix}-v1"

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "kind": SACRIFICE_COST_KIND,
            "count": 1,
            "choice_field": "sacrifice_cards",
            "predicate": _permanent_you_control_query(
                self.permanent_types
            ).to_dict(),
        }

    @property
    def cost_schema(self) -> Mapping[str, Any]:
        return {"additional_costs": [dict(self.descriptor)]}


@dataclass(frozen=True, slots=True)
class FixedZoneChangeAdditionalCostTemplate:
    """One mandatory single-object zone change paid while casting."""

    operation: str
    predicate: ObjectQuerySpec

    def __post_init__(self) -> None:
        if self.operation not in FIXED_ZONE_CHANGE_COST_CONTRACTS:
            raise ValueError("Zone-change additional-cost operation is unsupported")
        origin, _, _ = FIXED_ZONE_CHANGE_COST_CONTRACTS[self.operation]
        if self.predicate.zones != (origin,):
            raise ValueError("Zone-change additional-cost origin is noncanonical")

    @property
    def template_id(self) -> str:
        terms: list[str] = [self.operation.replace("_one", "")]
        for field_name in (
            "types_all",
            "types_any",
            "excluded_types",
            "subtypes_all",
            "subtypes_any",
            "supertypes_all",
            "colors_all",
            "colors_any",
        ):
            values = getattr(self.predicate, field_name)
            if values:
                terms.append(field_name.replace("_", "-"))
                terms.extend(str(value).casefold() for value in values)
        if self.predicate.token is not None:
            terms.append("token" if self.predicate.token else "nontoken")
        if len(terms) == 1:
            terms.append("card")
        return "spell-additional-cost-fixed-" + "-".join(terms) + "-v1"

    @property
    def descriptor(self) -> Mapping[str, Any]:
        _, _, choice_field = FIXED_ZONE_CHANGE_COST_CONTRACTS[self.operation]
        return {
            "schema_version": 1,
            "kind": ZONE_CHANGE_COST_KIND,
            "operation": self.operation,
            "count": 1,
            "choice_field": choice_field,
            "predicate": self.predicate.to_dict(),
        }

    @property
    def cost_schema(self) -> Mapping[str, Any]:
        return {"additional_costs": [dict(self.descriptor)]}


@dataclass(frozen=True, slots=True)
class FixedLifePaymentAdditionalCostTemplate:
    """One positive fixed life payment made while casting a spell."""

    amount: int

    def __post_init__(self) -> None:
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Fixed life additional-cost amount must be positive"
            )

    @property
    def template_id(self) -> str:
        return f"spell-additional-cost-fixed-life-{self.amount}-v1"

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "kind": FIXED_LIFE_PAYMENT_COST_KIND,
            "amount": self.amount,
        }

    @property
    def cost_schema(self) -> Mapping[str, Any]:
        return {"additional_costs": [dict(self.descriptor)]}


@dataclass(frozen=True, slots=True)
class FixedManaPaymentAdditionalCostTemplate:
    """One positive ordinary fixed mana payment used as a cost leaf."""

    requirements: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        requirements = dict(self.requirements)
        expected = ("GENERIC", "W", "U", "B", "R", "G", "C")
        if (
            tuple(key for key, _ in self.requirements) != expected
            or len(requirements) != len(expected)
            or any(
                type(amount) is not int or amount < 0
                for amount in requirements.values()
            )
            or not any(requirements.values())
        ):
            raise ValueError(
                "Fixed mana additional costs require a positive ordinary vector"
            )

    @property
    def template_id(self) -> str:
        terms = [
            f"{key.casefold()}-{amount}"
            for key, amount in self.requirements
            if amount
        ]
        return "spell-additional-cost-fixed-mana-" + "-".join(terms) + "-v1"

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "kind": FIXED_MANA_PAYMENT_COST_KIND,
            "requirements": dict(self.requirements),
        }


FixedAlternativeLeafTemplate = (
    FixedZoneChangeAdditionalCostTemplate
    | FixedLifePaymentAdditionalCostTemplate
    | FixedManaPaymentAdditionalCostTemplate
)


@dataclass(frozen=True, slots=True)
class FixedAlternativeAdditionalCostTemplate:
    """One printed binary choice among independently typed fixed costs."""

    options: tuple[FixedAlternativeLeafTemplate, ...]

    def __post_init__(self) -> None:
        if len(self.options) != 2:
            raise ValueError(
                "Fixed alternative additional costs require two options"
            )
        if self.options[0].descriptor == self.options[1].descriptor:
            raise ValueError(
                "Fixed alternative additional-cost options must be distinct"
            )

    @property
    def template_id(self) -> str:
        terms = [
            option.template_id
            .removeprefix("spell-additional-cost-")
            .removesuffix("-v1")
            for option in self.options
        ]
        return "spell-additional-cost-fixed-alternative-" + "-or-".join(
            terms
        ) + "-v1"

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "kind": ALTERNATIVE_ADDITIONAL_COST_KIND,
            "options": [dict(option.descriptor) for option in self.options],
        }

    @property
    def cost_schema(self) -> Mapping[str, Any]:
        return {"additional_costs": [dict(self.descriptor)]}


def _article_matches(article: str, noun: str) -> bool:
    expected = "an" if noun[0].casefold() in "aeiou" else "a"
    return article.casefold() == expected


def _owned_zone_query(
    zone: str,
    *,
    types_all: tuple[str, ...] = (),
    types_any: tuple[str, ...] = (),
    colors_any: tuple[str, ...] = (),
) -> ObjectQuerySpec:
    return ObjectQuerySpec(
        zones=(zone,),
        owner="$actor",
        types_all=types_all,
        types_any=types_any,
        colors_any=colors_any,
        known_to_actor=True,
    )


def _controlled_permanent_query(
    *,
    types_all: tuple[str, ...] = (),
    types_any: tuple[str, ...] = (),
    excluded_types: tuple[str, ...] = (),
    subtypes_all: tuple[str, ...] = (),
    subtypes_any: tuple[str, ...] = (),
    supertypes_all: tuple[str, ...] = (),
    colors_all: tuple[str, ...] = (),
    colors_any: tuple[str, ...] = (),
    token: bool | None = None,
) -> ObjectQuerySpec:
    return ObjectQuerySpec(
        zones=("battlefield",),
        controller="$actor",
        types_all=types_all,
        types_any=types_any,
        excluded_types=excluded_types,
        subtypes_all=subtypes_all,
        subtypes_any=subtypes_any,
        supertypes_all=supertypes_all,
        colors_all=colors_all,
        colors_any=colors_any,
        token=token,
        known_to_actor=True,
    )


def _fixed_sacrifice_subtype(value: str) -> str | None:
    return canonical_creature_subtype(value) or (
        value if value in _FIXED_NONCREATURE_SUBTYPES else None
    )


def _qualified_sacrifice_query(quality: str) -> ObjectQuerySpec | None:
    normalized = " ".join(quality.casefold().split())
    token: bool | None = None
    if normalized == "token":
        return _controlled_permanent_query(token=True)
    if normalized.startswith("nontoken "):
        normalized = normalized.removeprefix("nontoken ")
        token = False
    elif normalized.endswith(" token"):
        normalized = normalized.removesuffix(" token")
        token = True
    if token is not None and " or " in normalized:
        return None
    if normalized == "nonland permanent":
        return _controlled_permanent_query(
            excluded_types=("land",), token=token
        )
    if normalized == "noncreature artifact":
        return _controlled_permanent_query(
            types_all=("artifact",),
            excluded_types=("creature",),
            token=token,
        )
    if normalized == "artifact creature":
        return _controlled_permanent_query(
            types_all=("artifact", "creature"), token=token
        )
    supertype = next(
        (
            value
            for value in ("basic", "legendary", "snow")
            if normalized.startswith(value + " ")
        ),
        None,
    )
    if supertype is not None:
        subject = normalized.removeprefix(supertype + " ")
        if subject == "permanent":
            return _controlled_permanent_query(
                supertypes_all=(supertype,), token=token
            )
        if subject in _PERMANENT_TYPES:
            return _controlled_permanent_query(
                types_all=(subject,),
                supertypes_all=(supertype,),
                token=token,
            )
        subtype = _fixed_sacrifice_subtype(subject)
        if subtype is not None:
            return _controlled_permanent_query(
                subtypes_all=(subtype,),
                supertypes_all=(supertype,),
                token=token,
            )
        return None
    color_subject = re.fullmatch(
        r"(?P<colors>white|blue|black|red|green"
        r"(?: or (?:white|blue|black|red|green))?) "
        r"(?P<subject>creature|permanent)",
        normalized,
    )
    if color_subject is not None:
        colors = tuple(
            _COLOR_WORDS[value]
            for value in color_subject.group("colors").split(" or ")
        )
        return _controlled_permanent_query(
            types_all=(
                ()
                if color_subject.group("subject") == "permanent"
                else ("creature",)
            ),
            colors_all=colors if len(colors) == 1 else (),
            colors_any=colors if len(colors) == 2 else (),
            token=token,
        )
    terms = tuple(normalized.split(" or "))
    if len(terms) == 2 and all(term in _PERMANENT_TYPES for term in terms):
        return _controlled_permanent_query(types_any=terms, token=token)
    if len(terms) == 2:
        subtypes = tuple(_fixed_sacrifice_subtype(term) for term in terms)
        if all(subtype is not None for subtype in subtypes):
            return _controlled_permanent_query(
                subtypes_any=tuple(str(subtype) for subtype in subtypes),
                token=token,
            )
        return None
    if normalized == "permanent":
        return _controlled_permanent_query(token=token)
    if normalized in _PERMANENT_TYPES:
        return _controlled_permanent_query(
            types_all=(normalized,), token=token
        )
    subtype = _fixed_sacrifice_subtype(normalized)
    if subtype is not None:
        return _controlled_permanent_query(
            subtypes_all=(subtype,), token=token
        )
    adjacent_subtypes = tuple(
        canonical_creature_subtype(word) for word in normalized.split()
    )
    if len(adjacent_subtypes) == 2 and all(
        subtype is not None for subtype in adjacent_subtypes
    ):
        return _controlled_permanent_query(
            subtypes_all=tuple(str(subtype) for subtype in adjacent_subtypes),
            token=token,
        )
    return None


def fixed_zone_change_additional_cost_template(
    text: str,
) -> FixedZoneChangeAdditionalCostTemplate | None:
    """Parse the closed fixed single-object zone-change cost family."""

    stripped = text.strip()
    match = _FIXED_DISCARD_COST.fullmatch(stripped)
    if match is not None:
        raw_quality = match.group("quality")
        quality = raw_quality.casefold() if raw_quality else ""
        if not _article_matches(match.group("article"), quality or "card"):
            return None
        if not quality:
            predicate = _owned_zone_query("hand")
        elif quality in {"land", "creature"}:
            predicate = _owned_zone_query("hand", types_all=(quality,))
        else:
            colors = tuple(
                _COLOR_WORDS.get(value)
                for value in quality.split(" or ")
            )
            if any(value is None for value in colors):
                return None
            predicate = _owned_zone_query(
                "hand", colors_any=tuple(str(value) for value in colors)
            )
        return FixedZoneChangeAdditionalCostTemplate(
            DISCARD_ONE_COST, predicate
        )

    match = _FIXED_GRAVEYARD_EXILE_COST.fullmatch(stripped)
    if match is not None:
        raw_quality = match.group("quality")
        quality = raw_quality.casefold() if raw_quality else ""
        if not _article_matches(match.group("article"), quality or "card"):
            return None
        if not quality:
            predicate = _owned_zone_query("graveyard")
            return FixedZoneChangeAdditionalCostTemplate(
                EXILE_ONE_FROM_GRAVEYARD_COST, predicate
            )
        card_types = tuple(quality.split(" or "))
        if not set(card_types).issubset({"creature", "instant", "sorcery"}):
            return None
        predicate = _owned_zone_query(
            "graveyard",
            types_all=card_types if len(card_types) == 1 else (),
            types_any=card_types if len(card_types) > 1 else (),
        )
        return FixedZoneChangeAdditionalCostTemplate(
            EXILE_ONE_FROM_GRAVEYARD_COST, predicate
        )

    match = _FIXED_BATTLEFIELD_EXILE_COST.fullmatch(stripped)
    if match is not None:
        quality = " ".join(match.group("quality").casefold().split())
        if not _article_matches(match.group("article"), quality):
            return None
        if quality not in {"artifact", "creature", "permanent"}:
            return None
        predicate = _controlled_permanent_query(
            types_all=(() if quality == "permanent" else (quality,))
        )
        return FixedZoneChangeAdditionalCostTemplate(
            EXILE_ONE_FROM_BATTLEFIELD_COST, predicate
        )

    match = _FIXED_RETURN_COST.fullmatch(stripped)
    if match is not None:
        quality = " ".join(match.group("quality").casefold().split())
        if not _article_matches(match.group("article"), quality):
            return None
        if quality not in {"land", "creature", "permanent"}:
            return None
        predicate = _controlled_permanent_query(
            types_all=(() if quality == "permanent" else (quality,))
        )
        return FixedZoneChangeAdditionalCostTemplate(
            RETURN_ONE_TO_OWNER_HAND_COST, predicate
        )

    match = _QUALIFIED_SACRIFICE_COST.fullmatch(stripped)
    if match is None:
        return None
    quality = " ".join(match.group("quality").casefold().split())
    if not _article_matches(match.group("article"), quality):
        return None
    predicate = _qualified_sacrifice_query(quality)
    if predicate is None:
        return None
    return FixedZoneChangeAdditionalCostTemplate(
        SACRIFICE_ONE_COST, predicate
    )


def fixed_life_payment_additional_cost_template(
    text: str,
) -> FixedLifePaymentAdditionalCostTemplate | None:
    """Parse one mandatory positive fixed life payment."""

    match = _FIXED_LIFE_COST.fullmatch(text.strip())
    if match is None:
        return None
    amount = fixed_number(match.group("count"))
    if amount <= 0:
        return None
    return FixedLifePaymentAdditionalCostTemplate(amount)


def _fixed_mana_additional_cost_leaf_template(
    text: str,
) -> FixedManaPaymentAdditionalCostTemplate | None:
    match = _FIXED_MANA_LEAF.fullmatch(text.strip())
    if match is None:
        return None
    requirements, complex_symbols = mana_cost_to_vector(match.group("mana"))
    if complex_symbols or not any(requirements.values()):
        return None
    return FixedManaPaymentAdditionalCostTemplate(
        tuple(requirements.items())
    )


def _fixed_zone_change_additional_cost_leaf_template(
    text: str,
) -> FixedZoneChangeAdditionalCostTemplate | None:
    clause = (
        "As an additional cost to cast this spell, "
        + text.strip().removesuffix(".")
        + "."
    )
    typed = fixed_zone_change_additional_cost_template(clause)
    if typed is not None:
        return typed
    legacy = fixed_sacrifice_additional_cost_template(clause)
    if legacy is None:
        return None
    return FixedZoneChangeAdditionalCostTemplate(
        SACRIFICE_ONE_COST,
        ObjectQuerySpec.from_dict(legacy.descriptor["predicate"]),
    )


def _fixed_alternative_leaf_template(
    text: str,
) -> FixedAlternativeLeafTemplate | None:
    mana = _fixed_mana_additional_cost_leaf_template(text)
    if mana is not None:
        return mana
    life = fixed_life_payment_additional_cost_template(
        "As an additional cost to cast this spell, "
        + text.strip().removesuffix(".")
        + "."
    )
    if life is not None:
        return life
    return _fixed_zone_change_additional_cost_leaf_template(text)


def fixed_alternative_additional_cost_template(
    text: str,
) -> FixedAlternativeAdditionalCostTemplate | None:
    """Parse one unambiguous printed binary fixed additional-cost choice."""

    match = _ADDITIONAL_COST_CLAUSE.fullmatch(text.strip())
    if match is None:
        return None
    body = match.group("body").removesuffix(".")
    if (
        "(" in body
        or " and " in body.casefold()
        or body.casefold().startswith("you may ")
    ):
        return None
    candidates: list[FixedAlternativeAdditionalCostTemplate] = []
    for separator in re.finditer(r"\s+or\s+", body, re.IGNORECASE):
        first = _fixed_alternative_leaf_template(body[: separator.start()])
        second = _fixed_alternative_leaf_template(body[separator.end() :])
        if first is not None and second is not None:
            try:
                candidates.append(
                    FixedAlternativeAdditionalCostTemplate((first, second))
                )
            except ValueError:
                continue
    if len(candidates) != 1:
        return None
    return candidates[0]


def fixed_counter_additional_cost_template(
    text: str,
) -> FixedCounterAdditionalCostTemplate | None:
    """Parse one exact mandatory creature-counter casting cost."""

    match = _FIXED_COUNTER_COST.fullmatch(text.strip())
    if match is None:
        return None
    amount = fixed_number(match.group("count"))
    if amount <= 0 or (match.group("plural").casefold() == "counter") != (
        amount == 1
    ):
        return None
    return FixedCounterAdditionalCostTemplate(
        amount=amount,
        counter_name=match.group("counter"),
    )


def fixed_sacrifice_additional_cost_template(
    text: str,
) -> FixedSacrificeAdditionalCostTemplate | None:
    """Parse one exact fixed sacrifice casting cost with closed type nouns."""

    match = _FIXED_SACRIFICE_COST.fullmatch(text.strip())
    if match is None:
        return None
    first = match.group("first").casefold()
    article = match.group("article").casefold()
    expected_article = "an" if first[0] in "aeiou" else "a"
    if article != expected_article:
        return None
    second = match.group("second")
    if first == "permanent":
        if second is not None:
            return None
        types: tuple[str, ...] = ()
    else:
        types = (first,) if second is None else (first, second.casefold())
    return FixedSacrificeAdditionalCostTemplate(types)


__all__ = [
    "FixedAlternativeAdditionalCostTemplate",
    "FixedCounterAdditionalCostTemplate",
    "FixedLifePaymentAdditionalCostTemplate",
    "FixedManaPaymentAdditionalCostTemplate",
    "FixedSacrificeAdditionalCostTemplate",
    "FixedZoneChangeAdditionalCostTemplate",
    "fixed_counter_additional_cost_template",
    "fixed_alternative_additional_cost_template",
    "fixed_life_payment_additional_cost_template",
    "fixed_sacrifice_additional_cost_template",
    "fixed_zone_change_additional_cost_template",
]
