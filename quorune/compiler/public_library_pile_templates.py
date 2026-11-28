from __future__ import annotations

"""Compile closed printed public library-pile spell bodies."""

from ..library_search_model import FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID
from ..public_library_piles_model import (
    PILE_MECHANIC, PILE_OPERATION, distinct_public_search_split_effect, public_search_split_effect,
    public_search_split_target_schema,
)


_DIVIDER_TEXT = (
    "Reveal the top five cards of your library. An opponent separates those cards into two piles. "
    "Put one pile into your hand and the other into your graveyard."
)
_CHOOSER_TEXT = (
    "Reveal the top five cards of your library and separate them into two piles. "
    "An opponent chooses one of those piles. "
    "Put that pile into your hand and the other into your graveyard."
)
_PRINTED_ROLES = {
    _DIVIDER_TEXT.casefold(): "divider",
    _CHOOSER_TEXT.casefold(): "chooser",
}


_SEARCHED_SPLIT_TEXT = (
    "Search your library for three cards and reveal them. Target opponent chooses one. "
    "Put that card into your hand and the rest into your graveyard. Then shuffle."
)
_DISTINCT_SEARCHED_SPLIT_TEXT = (
    "Search your library for up to four cards with different names and reveal them. "
    "Target opponent chooses two of those cards. Put the chosen cards into your graveyard "
    "and the rest into your hand. Then shuffle."
)


def public_library_piles_template(text: str):
    normalized = text.strip().casefold()
    if normalized == _SEARCHED_SPLIT_TEXT.casefold():
        return ("public-library-search-opponent-split-v31", (public_search_split_effect(),),
                public_search_split_target_schema(),
                (PILE_MECHANIC, FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID, "cr-115-targets"))
    if normalized == _DISTINCT_SEARCHED_SPLIT_TEXT.casefold():
        return ("public-library-distinct-search-opponent-split-v32", (distinct_public_search_split_effect(),),
                public_search_split_target_schema(),
                (PILE_MECHANIC, FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID, "cr-115-targets"))
    role = _PRINTED_ROLES.get(normalized)
    if role is None:
        return None
    effect = {
        "op": PILE_OPERATION,
        "player": "$controller",
        "schema_version": 1,
        "look_count": 5,
        "opponent_role": role,
    }
    return "public-library-piles-v1", (effect,), None, (PILE_MECHANIC,)
