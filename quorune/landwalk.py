from __future__ import annotations

from dataclasses import dataclass


from .landwalk_vocabulary import (
    BASIC_LANDWALK_TYPES, BASIC_LAND_TYPES, QUALIFIED_LANDWALK_REQUIREMENTS,
    QUALIFIED_LANDWALK_KEYWORDS, LANDWALK_KEYWORDS,
    LANDWALK_REQUIREMENTS as _LANDWALK_REQUIREMENTS,
)
_SUPPORTED_KEYWORDS = frozenset(keyword for keyword, _ in BASIC_LANDWALK_TYPES)


class LandwalkRuleError(ValueError):
    """A landwalk snapshot or unsupported landwalk variant is malformed."""


def _canonical_terms(value: object, label: str) -> frozenset[str]:
    if type(value) is not frozenset or any(
        not isinstance(term, str)
        or not term
        or term != term.strip()
        or term != term.casefold()
        for term in value
    ):
        raise LandwalkRuleError(f"Canonical {label} snapshot is malformed")
    return value


@dataclass(frozen=True, slots=True)
class LandwalkLandCharacteristics:
    """Current effective traits of one defending phased-in public land."""

    card_types: frozenset[str]
    subtypes: frozenset[str]
    supertypes: frozenset[str]

    def __post_init__(self) -> None:
        for field in ("card_types", "subtypes", "supertypes"):
            _canonical_terms(getattr(self, field), field)
        if "land" not in self.card_types:
            raise LandwalkRuleError("A defending land snapshot must be a land")


def _represented_keywords(keywords, vocabulary):
    keywords = _canonical_terms(keywords, "attacker keyword")
    represented = keywords.intersection(vocabulary)
    unsupported = {keyword for keyword in keywords
        if (keyword == "landwalk" or keyword.endswith("walk"))
        and keyword not in vocabulary}
    if represented:
        unsupported.discard("landwalk")
    if unsupported:
        raise LandwalkRuleError("Unsupported landwalk variant(s): "
            + ", ".join(sorted(unsupported)))
    return represented


def _matching_keywords(represented, lands):
    return tuple(keyword for keyword, subtype, card_type, supertype, excluded in _LANDWALK_REQUIREMENTS
        if keyword in represented and any(
            (subtype is None or subtype in land.subtypes)
            and (card_type is None or card_type in land.card_types)
            and (supertype is None or supertype in land.supertypes)
            and (excluded is None or excluded not in land.supertypes)
            for land in lands))


def _ignored_keywords(value):
    ignored = _canonical_terms(value, "ignored Landwalk keyword")
    if not ignored.issubset(LANDWALK_KEYWORDS):
        raise LandwalkRuleError("Ignored Landwalk keywords exceed the represented vocabulary")
    return ignored


@dataclass(frozen=True, slots=True)
class LandwalkBlockVerdict:
    matching_keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (type(self.matching_keywords) is not tuple
                or self.matching_keywords != tuple(keyword for keyword in LANDWALK_KEYWORDS
                    if keyword in self.matching_keywords)):
            raise LandwalkRuleError("Matching Landwalk keywords must be unique and canonical")

    @property
    def allowed(self) -> bool:
        return not self.matching_keywords

    @property
    def reason(self) -> str | None:
        return ("attacker_has_" + self.matching_keywords[0].replace(" ", "_")
            if self.matching_keywords else None)


def landwalk_block_verdict(
    attacker_keywords: frozenset[str],
    defending_lands: tuple[LandwalkLandCharacteristics, ...],
    *, ignored_keywords: frozenset[str] = frozenset(),
) -> LandwalkBlockVerdict:
    represented = _represented_keywords(attacker_keywords, frozenset(LANDWALK_KEYWORDS))
    ignored = _ignored_keywords(ignored_keywords)
    if type(defending_lands) is not tuple or any(
        not isinstance(land, LandwalkLandCharacteristics) for land in defending_lands
    ):
        raise LandwalkRuleError("Landwalk requires typed individual defending lands")
    return LandwalkBlockVerdict(_matching_keywords(represented - ignored, defending_lands))


@dataclass(frozen=True, slots=True)
class BasicLandwalkBlockVerdict:
    """Closed result for the five represented basic-land-type variants."""

    allowed: bool
    matching_land_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise LandwalkRuleError("Landwalk verdict allowed must be boolean")
        if tuple(self.matching_land_types) != self.matching_land_types:
            raise LandwalkRuleError("Matching land types must be a tuple")
        expected = tuple(
            land_type
            for _, land_type in BASIC_LANDWALK_TYPES
            if land_type in self.matching_land_types
        )
        if expected != self.matching_land_types or len(expected) != len(
            set(expected)
        ):
            raise LandwalkRuleError(
                "Matching land types must be unique and canonical"
            )
        if self.allowed != (not self.matching_land_types):
            raise LandwalkRuleError(
                "A rejected landwalk verdict requires a matching land type"
            )

    @property
    def reason(self) -> str | None:
        if self.allowed:
            return None
        return f"attacker_has_{self.matching_land_types[0]}walk"


def basic_landwalk_block_verdict(
    attacker_keywords: frozenset[str],
    defending_land_types: frozenset[str],
    *, ignored_keywords: frozenset[str] = frozenset(),
) -> BasicLandwalkBlockVerdict:
    """Apply ordinary Plains/Island/Swamp/Mountain/Forestwalk.

    The inputs are current effective public characteristics. A matching land
    need not have the Basic supertype. Broader landwalk variants deliberately
    fail closed until their predicates have typed owners.
    """

    represented = _represented_keywords(attacker_keywords, _SUPPORTED_KEYWORDS)
    land_types = _canonical_terms(defending_land_types, "land type")
    if not land_types.issubset(BASIC_LAND_TYPES):
        raise LandwalkRuleError(
            "Defending land types exceed the closed vocabulary"
        )

    lands = tuple(LandwalkLandCharacteristics(frozenset({"land"}),
        frozenset({land_type}), frozenset()) for land_type in sorted(land_types))
    matching = _matching_keywords(represented - _ignored_keywords(ignored_keywords), lands)
    matches = tuple(land_type for keyword, land_type in BASIC_LANDWALK_TYPES if keyword in matching)
    return BasicLandwalkBlockVerdict(
        allowed=not matches,
        matching_land_types=matches,
    )


__all__ = [
    "BASIC_LAND_TYPES",
    "BASIC_LANDWALK_TYPES",
    "BasicLandwalkBlockVerdict",
    "LandwalkRuleError",
    "basic_landwalk_block_verdict",
    "LANDWALK_KEYWORDS",
    "QUALIFIED_LANDWALK_KEYWORDS",
    "LandwalkLandCharacteristics",
    "LandwalkBlockVerdict",
    "landwalk_block_verdict",
]
