"""One fixed conjunction or disjunction over a card's library characteristics."""

from . import regex_working_set as re
from ..object_predicate import ObjectQuerySpec
from ..qualified_library_search_model import (
    CREATURE_KEYWORD_TOP_SEARCH_KEYWORDS, NONLEGENDARY_LIBRARY_CARD_QUERY, QualifiedLibrarySearchSpec,
)
from ..target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison
from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC, FixedOptionalEffectTemplate

_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
_SEARCH = re.compile(r"Search your library for an? (?P<quality>.+?) card(?P<after>.*?)"
    r", (?P<reveal>reveal it, )?put it (?P<destination>into your hand|onto the battlefield), then shuffle\.", re.I)


_CREATURE_STAT_SEARCH = re.compile(
    r"(?P<optional>you may )?search your library for a creature card with "
    r"(?P<stat>power|toughness) 2 or less, reveal it, put it into your hand, then shuffle\.",
    re.IGNORECASE,
)


def creature_stat_library_search_template(text: str):
    match = _CREATURE_STAT_SEARCH.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    stat = TargetNumericCharacteristic(match["stat"].casefold())
    optional = match["optional"] is not None
    if optional != (stat is TargetNumericCharacteristic.TOUGHNESS):
        return None
    template, effects, targets, mechanics = QualifiedLibrarySearchSpec(
        ObjectQuerySpec(zones=("library",), types_all=("creature",)),
        "hand",
        True,
        stat_bound=TargetNumericCharacteristicSpec(stat, TargetNumericComparison.AT_MOST, 2),
    ).compiled()
    if not optional:
        return template, effects, targets, mechanics
    return FixedOptionalEffectTemplate(
        component_template_id=template,
        _component_effects=effects,
        _target_schema=targets,
        mechanic_ids=(FIXED_OPTIONAL_EFFECT_MECHANIC, *mechanics),
    ).compiled()


_KEYWORD_TOP_SEARCH = (
    "search your library for a creature card with deathtouch, hexproof, reach, or trample "
    "and reveal it. shuffle and put that card on top."
)


_NONLEGENDARY_GRAVEYARD_SEARCH = re.compile(
    r"Search your library for a nonlegendary card, put (?:it|that card) into your graveyard, then shuffle\.", re.I,
)


def qualified_library_search_template(text):
    normalized = " ".join(text.strip().split())
    if _NONLEGENDARY_GRAVEYARD_SEARCH.fullmatch(normalized):
        return QualifiedLibrarySearchSpec(NONLEGENDARY_LIBRARY_CARD_QUERY, "graveyard", False)
    if normalized.casefold() == _KEYWORD_TOP_SEARCH:
        return QualifiedLibrarySearchSpec(
            ObjectQuerySpec(zones=("library",), types_all=("creature",)), "library_top", True,
            keywords_any=CREATURE_KEYWORD_TOP_SEARCH_KEYWORDS,
        )
    match = _SEARCH.fullmatch(normalized)
    if match is None:return None
    quality = match["quality"].casefold();after = match["after"].casefold()
    query = None;maximum = None;values = ()
    if quality == "artifact creature" and not after:
        query = ObjectQuerySpec(zones=("library",), types_all=("artifact", "creature"))
    elif quality == "creature" and after == " with defender":
        query = ObjectQuerySpec(zones=("library",), types_all=("creature",), keywords_all=("defender",))
    elif quality in {"nonlegendary " + color + " creature" for color in _COLORS}:
        bound = re.fullmatch(r" with mana value (?P<number>[0-9]+) or less", after)
        if bound is None:return None
        query = ObjectQuerySpec(zones=("library",), types_all=("creature",),
            colors_any=(_COLORS[quality.split()[1]],), excluded_supertypes=("legendary",))
        maximum = int(bound["number"])
    elif quality == "artifact":
        bound = re.fullmatch(r" with mana value (?P<first>[0-9]+) or (?P<second>[0-9]+)", after)
        if bound is None:return None
        values = tuple(sorted((int(bound["first"]), int(bound["second"]))))
        if len(set(values)) != 2:return None
        query = ObjectQuerySpec(zones=("library",), types_all=("artifact",))
    elif quality == "aura or shrine" and not after:
        query = ObjectQuerySpec(zones=("library",), subtypes_any=("aura", "shrine"))
    if query is None:return None
    try:
        return QualifiedLibrarySearchSpec(query, "hand" if match["destination"].casefold() == "into your hand" else "battlefield",
            bool(match["reveal"]), maximum, values)
    except ValueError:return None
