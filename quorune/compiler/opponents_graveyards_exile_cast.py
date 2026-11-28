"""Compile the complete opponent graveyard exile and cast instruction."""

from . import regex_working_set as re
from ..replacement.immutable import thaw_value
from ..rules.opponents_graveyards_exile_cast import INSTRUCTION, MECHANIC, TEMPLATE

_TEXT = re.compile(
    r"Exile all opponents['’] graveyards\. You may cast spells from among those cards this turn, "
    r"and mana of any type can be spent to cast them\. At the beginning of the next end step, "
    r"if any of those cards remain exiled, return them to their owners['’] graveyards\.", re.I,
)


def opponents_graveyards_exile_cast_template(text):
    if _TEXT.fullmatch(" ".join(text.split())) is None:
        return None
    return TEMPLATE, (thaw_value(INSTRUCTION),), None, (MECHANIC,)
