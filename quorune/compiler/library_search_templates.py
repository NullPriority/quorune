from __future__ import annotations

"""Closed Oracle lowering for fixed library searches to the battlefield."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..creature_subtypes import CREATURE_SUBTYPES
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..library_search_model import (
    FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    FixedCountedLibrarySearchTemplate,
    counted_search_selector_is_closed,
    search_selector,
    SEARCH_LAND_SUBTYPES,
    SEARCH_NONCREATURE_SUBTYPES,
    SEARCH_SUBTYPES,
)
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


FIXED_LIBRARY_SEARCH_MECHANIC_ID = "fixed-library-search-to-battlefield"
FIXED_LIBRARY_SEARCH_CAPABILITY_ID = "library.search.fixed_to_battlefield"
FIXED_LIBRARY_SEARCH_TO_HAND_MECHANIC_ID = "fixed-library-search-to-hand"

_BASIC_LAND_SUBTYPES = frozenset(
    {"plains", "island", "swamp", "mountain", "forest"}
)
_LAND_SUBTYPES = SEARCH_LAND_SUBTYPES
_PERMANENT_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)
_CARD_TYPES = _PERMANENT_TYPES | frozenset({"instant", "kindred", "sorcery"})
_NONCREATURE_SUBTYPES = SEARCH_NONCREATURE_SUBTYPES
_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_FIXED_SEARCH = re.compile(
    rf"^Search your library for (?P<up_to>up to )?"
    rf"(?P<count>an|{FIXED_COUNT_PATTERN}) "
    rf"(?P<quality>.+?) card(?P<plural>s)?, "
    rf"(?:(?P<reveal>reveal (?P<reveal_pronoun>it|them|that card|those cards), ))?"
    rf"put (?P<pronoun>it|them|that card|those cards) "
    rf"(?:(?P<hand>into your hand)|onto the battlefield(?P<tapped> tapped)?), "
    rf"then shuffle\.$",
    re.IGNORECASE,
)
_COUNTED_START = (
    rf"Search your library for (?P<optional>up to )?"
    rf"(?P<count>an|{FIXED_COUNT_PATTERN}) "
    r"(?:(?P<quality>.+?) )?card(?P<plural>s)?"
    r"(?: named (?P<name>.+?))?"
    r"(?: with mana value (?P<mv>[0-9]+)"
    r"(?: (?P<comparison>or less|or greater))?)?, "
)
_COUNTED_REVEAL = r"(?P<reveal>reveal (?P<reveal_pronoun>it|that card|them|those cards), )?"
_COUNTED_MOVE = re.compile(
    _COUNTED_START + _COUNTED_REVEAL
    + r"put (?P<pronoun>it|that card|them|those cards) "
    r"(?P<destination>into your hand|into your graveyard|"
    r"onto the battlefield(?: tapped)?), then shuffle\.", re.I,
)
_COUNTED_TOP = re.compile(
    _COUNTED_START + _COUNTED_REVEAL
    + r"then shuffle and put (?P<pronoun>it|that card) on top\.", re.I,
)


def _or_terms(value: str) -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in re.split(r",\s*(?:or\s+)?|\s+or\s+", value)
        if part.strip()
    )


def _permanent_query() -> ObjectQuerySpec:
    return ObjectQuerySpec(types_any=tuple(sorted(_PERMANENT_TYPES)))


def _search_query(
    quality: str,
    *,
    destination: str,
) -> ObjectQuerySpec | None:
    normalized = " ".join(quality.casefold().split())
    if normalized == "basic land":
        return ObjectQuerySpec(
            types_all=("land",),
            supertypes_all=("basic",),
        )
    if normalized == "land":
        return ObjectQuerySpec(types_all=("land",))
    if normalized == "snow land":
        return ObjectQuerySpec(
            types_all=("land",),
            supertypes_all=("snow",),
        )
    if normalized == "land with a basic land type":
        return ObjectQuerySpec(
            types_all=("land",),
            subtypes_any=tuple(sorted(_BASIC_LAND_SUBTYPES)),
        )

    basic = normalized.startswith("basic ")
    terms = _or_terms(normalized.removeprefix("basic "))
    if terms and all(term in _LAND_SUBTYPES for term in terms):
        return ObjectQuerySpec(
            types_all=("land",),
            subtypes_any=terms,
            supertypes_all=(("basic",) if basic else ()),
        )
    if basic:
        return None

    if normalized == "permanent":
        return _permanent_query()
    if normalized in _PERMANENT_TYPES:
        return ObjectQuerySpec(types_all=(normalized,))
    if normalized == "equipment":
        return ObjectQuerySpec(
            types_all=("artifact",),
            subtypes_any=("equipment",),
        )
    if normalized == "aura":
        return ObjectQuerySpec(
            types_all=("enchantment",),
            subtypes_any=("aura",),
        )
    if normalized == "legendary" and destination == "hand":
        return ObjectQuerySpec(supertypes_all=("legendary",))
    if normalized.startswith("legendary "):
        subject = normalized.removeprefix("legendary ")
        if subject in _CARD_TYPES and (
            destination == "hand" or subject in _PERMANENT_TYPES
        ):
            return ObjectQuerySpec(
                types_all=(subject,),
                supertypes_all=("legendary",),
            )
        if subject.endswith(" permanent"):
            subtype = subject.removesuffix(" permanent").strip()
            if subtype in SEARCH_SUBTYPES and " " not in subtype:
                return ObjectQuerySpec(
                    types_any=tuple(sorted(_PERMANENT_TYPES)),
                    subtypes_any=(subtype,),
                    supertypes_all=("legendary",),
                )
        return None
    if normalized.endswith(" creature"):
        qualifier = normalized.removesuffix(" creature").strip()
        if qualifier in _COLORS:
            return ObjectQuerySpec(
                types_all=("creature",),
                colors_any=(_COLORS[qualifier],),
            )
        if qualifier in CREATURE_SUBTYPES:
            return ObjectQuerySpec(
                types_all=("creature",),
                subtypes_any=(qualifier,),
            )
    if normalized.endswith(" permanent"):
        qualifier = normalized.removesuffix(" permanent").strip()
        if qualifier in _COLORS:
            return ObjectQuerySpec(
                types_any=tuple(sorted(_PERMANENT_TYPES)),
                colors_any=(_COLORS[qualifier],),
            )
        if qualifier in SEARCH_SUBTYPES and " " not in qualifier:
            return ObjectQuerySpec(
                types_any=tuple(sorted(_PERMANENT_TYPES)),
                subtypes_any=(qualifier,),
            )
        return None

    if destination == "hand":
        if normalized in _CARD_TYPES:
            return ObjectQuerySpec(types_all=(normalized,))
        if normalized in _COLORS:
            return ObjectQuerySpec(colors_any=(_COLORS[normalized],))
        colored_type = re.fullmatch(
            r"(?P<color>white|blue|black|red|green) "
            r"(?P<card_type>artifact|battle|creature|enchantment|instant|kindred|land|planeswalker|sorcery)",
            normalized,
        )
        if colored_type is not None:
            return ObjectQuerySpec(
                types_all=(colored_type.group("card_type"),),
                colors_any=(_COLORS[colored_type.group("color")],),
            )
        if normalized in CREATURE_SUBTYPES | _NONCREATURE_SUBTYPES:
            return ObjectQuerySpec(subtypes_any=(normalized,))

    type_terms = tuple(
        part.strip()
        for part in re.split(r"\s+and/or\s+|\s+or\s+", normalized)
        if part.strip()
    )
    if type_terms and all(term in _PERMANENT_TYPES for term in type_terms):
        return ObjectQuerySpec(types_any=type_terms)
    if destination == "hand" and type_terms and all(
        term in _CARD_TYPES for term in type_terms
    ):
        return ObjectQuerySpec(types_any=type_terms)
    if destination == "hand" and type_terms and all(
        term in CREATURE_SUBTYPES | _NONCREATURE_SUBTYPES
        for term in type_terms
    ):
        return ObjectQuerySpec(subtypes_any=type_terms)
    return None


def _selector(query: ObjectQuerySpec) -> dict[str, list[str]]:
    return search_selector(query)


def fixed_counted_library_search_effect_template(
    text: str,
) -> FixedCountedLibrarySearchTemplate | None:
    normalized = " ".join(text.strip().split())
    match = _COUNTED_MOVE.fullmatch(normalized) or _COUNTED_TOP.fullmatch(normalized)
    if match is None:
        return None
    count = fixed_number(match["count"])
    singular = count == 1
    pronouns = {"it", "that card"}
    if (
        not 1 <= count <= 10 or singular == bool(match["plural"])
        or singular != (match["pronoun"].casefold() in pronouns)
    ):
        return None
    if match["reveal_pronoun"] and singular != (match["reveal_pronoun"].casefold() in pronouns):
        return None
    top = match.re is _COUNTED_TOP
    destination = "library_top" if top else {
        "into your hand": "hand", "into your graveyard": "graveyard",
        "onto the battlefield": "battlefield", "onto the battlefield tapped": "battlefield",
    }[match["destination"].casefold()]
    if count > 1 and destination != "hand":
        return None
    quality = match["quality"] or ""
    query = (
        _search_query(
            quality, destination="battlefield" if destination == "battlefield" else "hand",
        ) if quality else ObjectQuerySpec()
    )
    if query is None:
        return None
    selector: dict[str, Any] = _selector(query)
    if match["name"] is not None:
        name = match["name"].strip()
        if (
            not name or not name[0].isupper()
            or any(value in name.casefold() for value in (" or ", "chosen", "same name", "this way"))
        ):
            return None
        selector["names"] = [name]
    if match["mv"] is not None:
        comparison = match["comparison"].casefold() if match["comparison"] else None
        key = {None: "equal", "or less": "maximum", "or greater": "minimum"}[comparison]
        selector["mana_value"] = {key: int(match["mv"])}
    try:
        return FixedCountedLibrarySearchTemplate(
            count=count, optional_count=bool(match["optional"]),
            selector=FrozenMap(selector), destination=destination,
            reveal=bool(match["reveal"]),
            enters_tapped=not top and match["destination"].casefold().endswith("tapped"),
        )
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class FixedLibrarySearchTemplate:
    count: int
    optional_count: bool
    query: ObjectQuerySpec
    destination: str
    reveal: bool
    enters_tapped: bool

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        effect: dict[str, Any] = {
            "op": "search",
            "zone": "library",
            "selector": _selector(self.query),
            "count": {
                "minimum": 0 if self.optional_count else self.count,
                "maximum": self.count,
            },
            "destination": self.destination,
            "shuffle_after": True,
        }
        if self.destination == "hand":
            effect["reveal"] = self.reveal
        if self.destination == "battlefield" and self.enters_tapped:
            effect["enters_tapped_override"] = True
        to_hand = self.destination == "hand"
        return (
            (
                "fixed-library-search-to-hand-v1"
                if to_hand
                else "fixed-library-search-to-battlefield-v1"
            ),
            (effect,),
            None,
            (
                (FIXED_LIBRARY_SEARCH_TO_HAND_MECHANIC_ID,)
                if to_hand
                else (FIXED_LIBRARY_SEARCH_MECHANIC_ID,)
            ),
        )


def fixed_library_search_effect_template(
    text: str,
) -> FixedLibrarySearchTemplate | FixedCountedLibrarySearchTemplate | None:
    """Lower one fixed restrictive library search directly to the battlefield."""

    match = _FIXED_SEARCH.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return fixed_counted_library_search_effect_template(text)
    count = fixed_number(match.group("count"))
    if not 1 <= count <= 10:
        return None
    singular = count == 1
    singular_pronouns = {"it", "that card"}
    if singular != (match.group("pronoun").casefold() in singular_pronouns):
        return None
    reveal_pronoun = match.group("reveal_pronoun")
    if reveal_pronoun is not None and singular != (
        reveal_pronoun.casefold() in singular_pronouns
    ):
        return None
    if singular == bool(match.group("plural")):
        return None
    destination = "hand" if match.group("hand") else "battlefield"
    if destination == "hand" and count != 1:
        return fixed_counted_library_search_effect_template(text)
    query = _search_query(
        match.group("quality"),
        destination=destination,
    )
    if query is None:
        return None
    enters_tapped = bool(match.group("tapped"))
    if count > 1 and not (
        enters_tapped
        and query.types_all == ("land",)
        and not query.types_any
    ):
        # Multiple battlefield entrants need one simultaneous transaction.
        # This closed production admits only the uniform tapped-land shape;
        # untapped and nonland groups can require independent entry choices.
        return None
    return FixedLibrarySearchTemplate(
        count=count,
        optional_count=bool(match.group("up_to")),
        query=query,
        destination=destination,
        reveal=bool(match.group("reveal")),
        enters_tapped=enters_tapped,
    )


__all__ = [
    "FIXED_LIBRARY_SEARCH_CAPABILITY_ID",
    "FIXED_LIBRARY_SEARCH_MECHANIC_ID",
    "FIXED_LIBRARY_SEARCH_TO_HAND_MECHANIC_ID",
    "FixedLibrarySearchTemplate",
    "FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID",
    "FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID",
    "FixedCountedLibrarySearchTemplate",
    "counted_search_selector_is_closed",
    "fixed_counted_library_search_effect_template",
    "fixed_library_search_effect_template",
]
