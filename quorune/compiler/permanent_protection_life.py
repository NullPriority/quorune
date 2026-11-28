from __future__ import annotations

from . import regex_working_set as re
from ..rules.permanent_protection_life import (
    PROTECTION_MECHANICS, TARGET_SCHEMA, controlled_protection_effect, target_protection_effects,
)


_PROTECTION = re.compile(
    r"(?P<subject>Target permanent gains|Permanents you control gain) "
    r"hexproof and indestructible until end of turn\. You gain (?P<amount>[1-9][0-9]?) life\.",
    re.IGNORECASE,
)


def permanent_protection_then_life_template(text):
    match = _PROTECTION.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    targeted = match["subject"].casefold().startswith("target ")
    effects = target_protection_effects() if targeted else (controlled_protection_effect(),)
    schema = dict(TARGET_SCHEMA) if targeted else None
    mechanics = (*PROTECTION_MECHANICS, "cr-119-life", *(("cr-115-targets",) if targeted else ()))
    life = {"op": "life", "player": "$controller", "delta": int(match["amount"])}
    return "fixed-permanent-protection-life-v1", (*effects, life), schema, mechanics
