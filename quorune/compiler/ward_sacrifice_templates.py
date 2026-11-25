"""Close only the represented printed Ward sacrifice prices."""

from __future__ import annotations

from ..trigger_participation import WardSacrificeScope
from . import regex_working_set as re


def ward_sacrifice_scope(text: str) -> WardSacrificeScope | None:
    material = " ".join(text.split())
    if re.fullmatch(r"Ward[—-]Sacrifice a creature\.?", material, re.IGNORECASE):
        return WardSacrificeScope.CREATURE
    if re.fullmatch(r"Ward[—-]Sacrifice a permanent with mana value 1 or greater\.?", material, re.IGNORECASE):
        return WardSacrificeScope.PERMANENT_WITH_POSITIVE_MANA_VALUE
    return None
