from __future__ import annotations

"""Fixed extra turns through the existing current-turn scheduling owner."""

import re

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number

FIXED_EXTRA_TURN_MECHANIC = "fixed-extra-turn-after-current"
FIXED_EXTRA_TURN_CAPABILITY = "turn.extra.fixed_after_current"

_EXTRA_TURN = re.compile(
    rf"(?P<subject>Take|Target player takes|Target opponent takes) "
    rf"(?P<count>an|{FIXED_COUNT_PATTERN}) extra turn(?P<plural>s)? after this one\.?",
    re.IGNORECASE,
)


def fixed_extra_turn_effect_template(text: str):
    match = _EXTRA_TURN.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    count = fixed_number(match["count"])
    if not 1 <= count <= 10 or bool(match["plural"]) != (count != 1):
        return None
    targeted = match["subject"].casefold() != "take"
    relation = "opponent" if "opponent" in match["subject"].casefold() else "any"
    schema = ({"zones": ["player"], "categories": ["player"],
        "player_relation": relation, "count": 1} if targeted else None)
    player = "$target.0" if targeted else "$controller"
    effects = tuple({"op": "extra_turn", "player": player, "source": "$source"} for _ in range(count))
    mechanics = (FIXED_EXTRA_TURN_MECHANIC, *(("cr-115-targets",) if targeted else ()))
    return "fixed-extra-turn-after-current-v1", effects, schema, mechanics
