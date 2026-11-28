"""Close only the represented printed Ward sacrifice prices."""

from __future__ import annotations

from ..trigger_participation import WardSacrificeScope
from . import regex_working_set as re


def ward_sacrifice_scope(text: str) -> WardSacrificeScope | None:
    material = " ".join(text.split())
    if re.fullmatch(r"Ward[—-]Sacrifice a creature\.?", material, re.IGNORECASE):
        return WardSacrificeScope.CREATURE
    if re.fullmatch(r"Ward[—-]Sacrifice a Food\.?", material, re.IGNORECASE):
        return WardSacrificeScope.FOOD_ARTIFACT
    if re.fullmatch(r"Ward[—-]Sacrifice a permanent with mana value 1 or greater\.?", material, re.IGNORECASE):
        return WardSacrificeScope.PERMANENT_WITH_POSITIVE_MANA_VALUE
    if re.fullmatch(r"Ward[—-]Sacrifice (?:two|2) permanents\.?", material, re.IGNORECASE):
        return WardSacrificeScope.TWO_PERMANENTS
    if re.fullmatch(r"Ward[—-]Sacrifice (?:three|3) nonland permanents\.?", material, re.IGNORECASE):
        return WardSacrificeScope.THREE_NONLAND_PERMANENTS
    return None
