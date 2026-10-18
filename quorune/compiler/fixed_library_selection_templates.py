from __future__ import annotations

"""Closed fixed-count library selection and remainder partitions."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..creature_subtypes import CREATURE_SUBTYPES
from .direct_target import DIRECT_NONCREATURE_SUBTYPES
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


FIXED_LIBRARY_SELECTION_CAPABILITY = "library.select.fixed_controller"
FIXED_LIBRARY_SELECTION_MECHANIC = "fixed-library-selection"

_CARD_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "instant",
        "kindred",
        "land",
        "planeswalker",
        "sorcery",
    }
)
_PERMANENT_TYPES = tuple(
    sorted(_CARD_TYPES - {"instant", "kindred", "sorcery"})
)
_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
# Literal planeswalker and spell subtype data from pinned CR 205.3j-k.
_PLANESWALKER_SUBTYPES = frozenset('''
ajani aminatou angrath arlinn ashiok bahamut basri bolas calix chandra comet dack
dakkon daretti davriel dellian dihada domri dovin ellywick elminster elspeth estrid
freyalise garruk gideon grist guff huatli jace jared jaya jeska kaito karn kasmina
kaya kiora koth liliana lolth lukka minsc mordenkainen nahiri narset niko nissa
nixilis oko quintorius ral rowan saheeli samut sarkhan serra sivitri sorin szat
tamiyo tasha teferi teyo tezzeret tibalt tyvar ugin urza venser vivien vraska vronos
will windgrace wrenn xenagos yanggu yanling zariel
'''.split())
LIBRARY_SELECTION_SUBTYPES = (CREATURE_SUBTYPES | DIRECT_NONCREATURE_SUBTYPES
    | _PLANESWALKER_SUBTYPES | {'adventure', 'arcane', 'lesson', 'omen', 'trap', 'siege'})

_START = re.compile(
    rf"(?P<verb>look at|reveal) the top "
    rf"(?P<count>{FIXED_COUNT_PATTERN}) cards? of your library\.",
    re.IGNORECASE,
)
_REMAINDER = re.compile(
    r"(?P<then>then )?(?:put )?the (?P<remainder>rest(?: of the cards)?|other) "
    r"(?P<destination>into your graveyard|on the bottom of your library"
    r"(?: in (?P<order>any|a random) order)?)\.?$",
    re.IGNORECASE,
)


def _query(**fields: Any) -> dict[str, Any]:
    return ObjectQuerySpec(zones=("library",), **fields).to_dict()


def _term_predicates(term: str) -> tuple[dict[str, Any], ...] | None:
    normalized = " ".join(term.strip().casefold().split())
    normalized = re.sub(r"^(?:a|an|one) ", "", normalized)
    normalized = re.sub(r" cards?$", "", normalized).replace('’', "'")
    if not normalized:
        return None
    if normalized == "historic":
        return (
            _query(types_any=("artifact",)),
            _query(supertypes_all=("legendary",)),
            _query(subtypes_any=("saga",)),
        )
    if normalized == "permanent":
        return (_query(types_any=_PERMANENT_TYPES),)
    if normalized == "nonland permanent":
        return (
            _query(
                types_any=_PERMANENT_TYPES,
                excluded_types=("land",),
            ),
        )
    if normalized == "colorless":
        return (_query(colorless=True),)
    if normalized in _COLORS:
        return (_query(colors_any=(_COLORS[normalized],)),)
    if normalized.startswith("legendary "):
        rest = normalized.removeprefix("legendary ")
        if rest in _CARD_TYPES:
            return (
                _query(
                    supertypes_all=("legendary",),
                    types_any=(rest,),
                ),
            )
        return None
    if normalized.startswith("snow "):
        rest = normalized.removeprefix("snow ")
        if rest == "permanent":
            return (
                _query(
                    supertypes_all=("snow",),
                    types_any=_PERMANENT_TYPES,
                ),
            )
        return None
    if normalized in _CARD_TYPES:
        return (_query(types_any=(normalized,)),)
    if normalized.startswith('non') and normalized[3:] in _CARD_TYPES:
        return (_query(excluded_types=(normalized[3:],)),)
    terms = normalized.split()
    if len(terms) > 1 and len(set(terms)) == len(terms) and all(term in _CARD_TYPES for term in terms):
        return (_query(types_all=tuple(terms)),)
    if normalized.endswith(' creature') and normalized[:-9] in CREATURE_SUBTYPES:
        return (_query(types_any=('creature',), subtypes_any=(normalized[:-9],)),)
    if normalized.startswith('non-') and normalized.endswith(' permanent'):
        subtype = normalized[4:-10]
        if subtype in LIBRARY_SELECTION_SUBTYPES:
            return (_query(types_any=_PERMANENT_TYPES, excluded_subtypes=(subtype,)),)
        return None
    if normalized in {'basic', 'legendary', 'snow'}:
        return (_query(supertypes_all=(normalized,)),)
    if normalized == 'basic land':
        return (_query(types_any=('land',), supertypes_all=('basic',)),)
    if terms and terms[0] in _COLORS and len(terms) > 1:
        quality = ' '.join(terms[1:])
        parsed = _term_predicates(quality)
        if parsed is not None and all(not predicate['colors_any'] and predicate['colorless'] is None
                                      for predicate in parsed):
            return tuple({**predicate, 'colors_any': [_COLORS[terms[0]]]} for predicate in parsed)
    if normalized in LIBRARY_SELECTION_SUBTYPES:
        return (_query(subtypes_any=(normalized,)),)
    return None


def _selector_alternatives(
    phrase: str,
    *,
    allow_and: bool = False,
) -> tuple[dict[str, Any], ...] | None:
    normalized = " ".join(phrase.strip().casefold().split())
    normalized = re.sub(r"\bcards?$", "", normalized).strip()
    if re.fullmatch(r"non[a-z]+, non[a-z]+", normalized):
        excluded = tuple(
            value.removeprefix("non")
            for value in normalized.split(", ")
        )
        if all(value in _CARD_TYPES for value in excluded):
            return (_query(excluded_types=excluded),)
        return None
    separators = r",\s*(?:or\s+)?|\s+(?:or|and/or)\s+"
    if allow_and:
        separators = r",\s*(?:or\s+)?|\s+(?:or|and/or|and)\s+"
    terms = tuple(
        term for term in re.split(separators, normalized) if term
    )
    if not terms:
        return None
    predicates: list[dict[str, Any]] = []
    for term in terms:
        parsed = _term_predicates(term)
        if parsed is None:
            return None
        predicates.extend(parsed)
    identities = {repr(value) for value in predicates}
    return tuple(predicates) if len(identities) == len(predicates) else None


@dataclass(frozen=True, slots=True)
class FixedLibrarySelectionEffectTemplate:
    look_count: int
    public_reveal: bool
    selected_reveal: bool
    selection_policy: str
    minimum: int
    maximum: int
    predicate_groups: tuple[tuple[Mapping[str, Any], ...], ...]
    remainder_destination: str
    remainder_order: str
    selected_destination: str='hand'

    def compiled(
        self,
    ) -> tuple[str, tuple[Mapping[str, Any], ...], None, tuple[str, ...]]:
        return (
            "fixed-library-selection-v1",
            (
                {
                    "op": "fixed_library_selection",
                    "player": "$controller",
                    "look_count": self.look_count,
                    "public_reveal": self.public_reveal,
                    "selected_reveal": self.selected_reveal,
                    "selection_policy": self.selection_policy,
                    "minimum": self.minimum,
                    "maximum": self.maximum,
                    "predicate_groups": [
                        [dict(predicate) for predicate in group]
                        for group in self.predicate_groups
                    ],
                    "remainder_destination": self.remainder_destination,
                    "remainder_order": self.remainder_order,
                    **({'schema_version':2,'selected_destination':self.selected_destination} if self.selected_destination!='hand' else {}),
                },
            ),
            None,
            (FIXED_LIBRARY_SELECTION_MECHANIC,),
        )


def _selection_shape(
    text: str,
    *,
    look_count: int,
) -> tuple[
    str,
    int,
    int,
    tuple[tuple[Mapping[str, Any], ...], ...],
    bool,
] | None:
    # Repeated independently quantified cards are slots, before a single union.
    slots = _optional_slots_shape(text)
    if slots is not None:
        return slots
    fixed = re.fullmatch(
        rf"put (?P<count>{FIXED_COUNT_PATTERN}) of "
        r"(?:them|those cards) into your hand(?:\.| and)?",
        text,
        re.IGNORECASE,
    )
    if fixed is not None:
        count = fixed_number(fixed.group("count"))
        if 0 < count <= look_count:
            return "fixed_any", count, count, (), False
        return None
    if look_count == 2 and re.fullmatch(r"put one into your hand(?:\.| and)?", text, re.I):
        return "fixed_any", 1, 1, (), False

    patterns = (
        (
            "optional_one",
            re.compile(
                r"you may reveal (?:a|an|one) (?P<selector>.+?) card "
                r"from among them and put (?:it|that card) into your hand\.?",
                re.IGNORECASE,
            ),
        ),
        (
            "optional_one",
            re.compile(
                r"you may put (?:a|an|one) (?P<selector>.+?) card "
                r"from among them into your hand\.?",
                re.IGNORECASE,
            ),
        ),
        (
            "mandatory_one",
            re.compile(
                r"put (?:a|an|one) (?P<selector>.+?) card "
                r"from among them into your hand(?:\.| and)?",
                re.IGNORECASE,
            ),
        ),
    )
    for policy, pattern in patterns:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        alternatives = _selector_alternatives(match.group("selector"))
        if alternatives is None:
            return None
        minimum = 1 if policy == "mandatory_one" else 0
        return (
            "up_to_matching",
            minimum,
            1,
            (alternatives,),
            "reveal" in text.casefold(),
        )

    bounded = re.fullmatch(
        rf"you may reveal up to (?P<count>{FIXED_COUNT_PATTERN}) "
        r"(?P<selector>.+?) cards from among them and put "
        r"(?:them|the revealed cards) into your hand\.?",
        text,
        re.IGNORECASE,
    )
    if bounded is None:
        bounded = re.fullmatch(
            rf"put up to (?P<count>{FIXED_COUNT_PATTERN}) "
            r"(?P<selector>.+?) cards from among them into your hand"
            r"(?:\.| and)?",
            text,
            re.IGNORECASE,
        )
    if bounded is not None:
        maximum = fixed_number(bounded.group("count"))
        alternatives = _selector_alternatives(
            bounded.group("selector"),
            allow_and=True,
        )
        if alternatives is None or not 0 < maximum <= look_count:
            return None
        return (
            "up_to_matching",
            0,
            maximum,
            (alternatives,),
            "reveal" in text.casefold(),
        )

    any_number = re.fullmatch(
        r"you may (?:reveal|put) any number of (?P<selector>.+?) cards "
        r"from among them(?: and put (?:them|the revealed cards) "
        r"into your hand)?\.?",
        text,
        re.IGNORECASE,
    )
    if any_number is not None:
        alternatives = _selector_alternatives(
            any_number.group("selector"),
            allow_and=True,
        )
        if alternatives is None:
            return None
        return (
            "up_to_matching",
            0,
            look_count,
            (alternatives,),
            "reveal" in text.casefold(),
        )

    all_matching = re.fullmatch(
        r"put all (?P<selector>.+?) cards revealed this way "
        r"into your hand(?:\.| and)?",
        text,
        re.IGNORECASE,
    )
    if all_matching is not None:
        alternatives = _selector_alternatives(
            all_matching.group("selector"),
            allow_and=True,
        )
        if alternatives is None:
            return None
        return "all_matching", 0, look_count, (alternatives,), False

    return _optional_slots_shape(text)


def _optional_slots_shape(
    text: str,
) -> tuple[
    str,
    int,
    int,
    tuple[tuple[Mapping[str, Any], ...], ...],
    bool,
] | None:
    slots = re.fullmatch(
        r"you may (?:reveal|put) (?:a|an) (?P<first>.+?) card "
        r"and/or (?:a|an) (?P<second>.+?) card(?: from among them)? "
        r"(?:and put (?:them|the revealed cards) into your hand|"
        r"from among them into your hand)\.?",
        text,
        re.IGNORECASE,
    )
    if slots is not None:
        groups = tuple(
            _selector_alternatives(slots.group(name))
            for name in ("first", "second")
        )
        if any(group is None for group in groups):
            return None
        return (
            "optional_slots",
            0,
            2,
            tuple(groups),  # type: ignore[arg-type]
            "reveal" in text.casefold(),
        )
    return None


def fixed_library_selection_effect_template(
    text: str,
) -> FixedLibrarySelectionEffectTemplate | None:
    """Lower one complete fixed self-library hand/remainder instruction."""

    normalized = " ".join(text.strip().split())
    top_exile=re.fullmatch(rf'Look at the top (?P<count>{FIXED_COUNT_PATTERN}) cards of your library\. '
        r'You may put one of those cards back on top of your library\. Exile the rest\.?',normalized,re.I)
    if top_exile is not None:
        count=fixed_number(top_exile['count'])
        if count<=0:return None
        return FixedLibrarySelectionEffectTemplate(count,False,False,'up_to_any',0,1,
            (),'exile','chosen','library_top')
    start = _START.match(normalized)
    remainder = _REMAINDER.search(normalized)
    if start is None or remainder is None or start.end() > remainder.start():
        return None
    selection_text = normalized[start.end() : remainder.start()].strip()
    shape = _selection_shape(
        selection_text,
        look_count=fixed_number(start.group("count")),
    )
    if shape is None:
        return None
    policy, minimum, maximum, groups, selected_reveal = shape
    if (remainder.group("remainder").casefold() == "other"
            and (policy != "fixed_any" or minimum != maximum
                 or fixed_number(start.group("count")) - maximum != 1)):
        return None
    destination_text = remainder.group("destination").casefold()
    destination = (
        "graveyard"
        if destination_text == "into your graveyard"
        else "library_bottom"
    )
    raw_order = (remainder.group("order") or "any").casefold()
    order = "random" if raw_order == "a random" else "chosen"
    return FixedLibrarySelectionEffectTemplate(
        look_count=fixed_number(start.group("count")),
        public_reveal=start.group("verb").casefold() == "reveal",
        selected_reveal=selected_reveal,
        selection_policy=policy,
        minimum=minimum,
        maximum=maximum,
        predicate_groups=groups,
        remainder_destination=destination,
        remainder_order=order,
    )


__all__ = [
    "FIXED_LIBRARY_SELECTION_CAPABILITY",
    "FIXED_LIBRARY_SELECTION_MECHANIC",
    "FixedLibrarySelectionEffectTemplate",
    "fixed_library_selection_effect_template",
]
