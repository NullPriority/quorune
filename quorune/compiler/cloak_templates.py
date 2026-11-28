"""Recognize the fixed typed top-card and ordered private Cloak forms."""

from . import regex_working_set as re
from ..rules.cloak_shapes import CLOAK_MECHANIC, cloak_library_effect

_TOP = re.compile(r"Cloak the top card of your library\.?", re.IGNORECASE)
_PARTITION = re.compile(
    r"Look at the top five cards of your library, cloak two of them, "
    r"and put the rest on the bottom of your library in a random order\.?",
    re.IGNORECASE,
)


def cloak_effect_template(text: str):
    material = " ".join(text.strip().split())
    if _TOP.fullmatch(material):
        return "fixed-cloak-top-v1", (cloak_library_effect("top"),), None, (CLOAK_MECHANIC,)
    if _PARTITION.fullmatch(material):
        return "private-cloak-partition-v1", (cloak_library_effect("top_five_choose_two"),), None, (CLOAK_MECHANIC,)
    return None
