"""Compile an announced-X multi-zone search with its unconditional resolution tail."""

from . import regex_working_set as re
from ..cast_x_search_model import (
    CAST_X_ZONE_SEARCH_MECHANIC, cast_x_zone_search_effect, cast_x_zone_search_pump,
)


_SEARCH_AND_PUMP = re.compile(
    r"Search your library and/or graveyard for a creature card with mana value X or less and put it onto "
    r"the battlefield\. If you search(?:ed)? your library this way, shuffle\. If X is (?P<threshold>[1-9][0-9]*) "
    r"or more, creatures you control get \+X/\+X and gain haste until end of turn\.", re.I,
)


def cast_x_zone_search_template(text, *, cast_x_available):
    if not cast_x_available:
        return None
    match = _SEARCH_AND_PUMP.fullmatch(" ".join(text.split()))
    if not match:
        return None
    effects = cast_x_zone_search_effect(), cast_x_zone_search_pump(int(match["threshold"]))
    return "cast-x-library-graveyard-search-pump-v1", effects, None, (CAST_X_ZONE_SEARCH_MECHANIC,)
