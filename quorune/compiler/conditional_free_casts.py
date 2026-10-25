from __future__ import annotations

import re

from ..public_alternative_costs import FixedPublicAlternativeCondition, FixedPublicAlternativeConditionKind


def conditional_zero_mana_condition(text: str):
    """Parse the closed intrinsic public zero-mana alternative sentence."""
    match = re.fullmatch(r'If an opponent controls (?:a|an) (?P<opponent>Forest|Island|Mountain|Plains|Swamp) and you control (?:a|an) (?P<own>Forest|Island|Mountain|Plains|Swamp), you may cast this spell without paying its mana cost\.', text.strip(), re.I)
    if match is not None:
        return FixedPublicAlternativeCondition(FixedPublicAlternativeConditionKind.LAND_PAIR,
            match['own'].casefold(), 2, match['opponent'].casefold())
    if re.fullmatch(r'If you control a commander, you may cast this spell without paying its mana cost\.', text.strip(), re.I):
        return FixedPublicAlternativeCondition(FixedPublicAlternativeConditionKind.CONTROLLED_COMMANDER, schema_version=2)
    return None
