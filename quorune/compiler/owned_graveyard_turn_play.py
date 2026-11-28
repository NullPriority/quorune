"""Compile the scoped graveyard-play and destination replacement pair."""

from . import regex_working_set as re
from ..replacement.immutable import thaw_value
from ..rules.owned_graveyard_turn_play import INSTRUCTION, MECHANIC, TEMPLATE


_PRINTED = re.compile(r"Until end of turn, you may play lands and cast spells from your graveyard\."
                      r" If a card would be put into your graveyard from anywhere this turn, exile that card instead\.",
                      re.IGNORECASE)


def owned_graveyard_turn_play_template(text):
    if _PRINTED.fullmatch(" ".join(text.split())) is None:
        return None
    return TEMPLATE, (thaw_value(INSTRUCTION),), None, (MECHANIC,)
