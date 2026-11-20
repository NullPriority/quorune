"""One fixed conjunction or disjunction over a card's library characteristics."""

from . import regex_working_set as re
from ..object_predicate import ObjectQuerySpec
from ..qualified_library_search_model import QualifiedLibrarySearchSpec

_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
_SEARCH = re.compile(r"Search your library for an? (?P<quality>.+?) card(?P<after>.*?)"
    r", (?P<reveal>reveal it, )?put it (?P<destination>into your hand|onto the battlefield), then shuffle\.", re.I)


def qualified_library_search_template(text):
    match = _SEARCH.fullmatch(" ".join(text.strip().split()))
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
