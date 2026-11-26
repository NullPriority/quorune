from __future__ import annotations

"""Compile the two complete printed public library-pile spell bodies."""

from ..public_library_piles_model import PILE_MECHANIC, PILE_OPERATION


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


def public_library_piles_template(text: str):
    role = _PRINTED_ROLES.get(text.strip().casefold())
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
