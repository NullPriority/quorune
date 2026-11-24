from __future__ import annotations

"""Fixed extra turns through the existing current-turn scheduling owner."""

from . import regex_working_set as re

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..public_history_query_model import SPELL_COLOR_HISTORY_FIELD

FIXED_EXTRA_TURN_MECHANIC = "fixed-extra-turn-after-current"
FIXED_EXTRA_TURN_CAPABILITY = "turn.extra.fixed_after_current"
EXTRA_TURN_END_STEP_LOSS_FIELD = "extra_turn_end_step_loss_version"
OPPONENT_CAST_EXTRA_TURN_COLOR_FIELD = "opponent_cast_color"
_COLOR_NAMES = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
_OPPONENT_COLOR_EXTRA_TURN = re.compile(
    rf"Take an extra turn after this one if an opponent cast a (?P<color>{'|'.join(_COLOR_NAMES)}) spell this turn\.",
    re.IGNORECASE,
)

_EXTRA_TURN_END_STEP_LOSS = re.compile(
    r"Take an extra turn after this one\. At the beginning of that turn['’]s end step, you lose the game\.?",
    re.I,
)
_LOCKED_INDESTRUCTIBLE_EXTRA_TURN = re.compile(
    r"Creatures you control gain indestructible\. " + _EXTRA_TURN_END_STEP_LOSS.pattern,
    re.I,
)
_EXTRA_TURN = re.compile(
    rf"(?P<subject>Take|You take|Target player takes|Target opponent takes) "
    rf"(?P<count>an|{FIXED_COUNT_PATTERN}) extra turn(?P<plural>s)? after this one\.?",
    re.IGNORECASE,
)


def fixed_extra_turn_end_step_loss_effect():
    return {"op": "extra_turn", "player": "$controller", "source": "$source",
            EXTRA_TURN_END_STEP_LOSS_FIELD: 1}


def opponent_color_extra_turn_effect(color: str):
    return {"op": "extra_turn", "player": "$controller", "source": "$source",
            OPPONENT_CAST_EXTRA_TURN_COLOR_FIELD: color, SPELL_COLOR_HISTORY_FIELD: 1}


def fixed_extra_turn_effect_template(text: str):
    normalized = " ".join(text.split())
    if match := _OPPONENT_COLOR_EXTRA_TURN.fullmatch(normalized):
        color = _COLOR_NAMES[match["color"].casefold()]
        return ("fixed-opponent-cast-color-extra-turn-v1", (opponent_color_extra_turn_effect(color),),
                None, (FIXED_EXTRA_TURN_MECHANIC,))
    if _LOCKED_INDESTRUCTIBLE_EXTRA_TURN.fullmatch(normalized):
        from ..zone_object_keyword_model import controlled_creature_indestructible_effect

        return ("locked-indestructible-extra-turn-loss-v1",
                (controlled_creature_indestructible_effect(), fixed_extra_turn_end_step_loss_effect()),
                None, (FIXED_EXTRA_TURN_MECHANIC,))
    if _EXTRA_TURN_END_STEP_LOSS.fullmatch(normalized):
        return ("fixed-extra-turn-end-step-loss-v1", (fixed_extra_turn_end_step_loss_effect(),),
                None, (FIXED_EXTRA_TURN_MECHANIC,))
    match = _EXTRA_TURN.fullmatch(normalized)
    if match is None:
        return None
    count = fixed_number(match["count"])
    if not 1 <= count <= 10 or bool(match["plural"]) != (count != 1):
        return None
    targeted = match["subject"].casefold() not in {"take", "you take"}
    relation = "opponent" if "opponent" in match["subject"].casefold() else "any"
    schema = ({"zones": ["player"], "categories": ["player"],
        "player_relation": relation, "count": 1} if targeted else None)
    player = "$target.0" if targeted else "$controller"
    effects = tuple({"op": "extra_turn", "player": player, "source": "$source"} for _ in range(count))
    mechanics = (FIXED_EXTRA_TURN_MECHANIC, *(("cr-115-targets",) if targeted else ()))
    return "fixed-extra-turn-after-current-v1", effects, schema, mechanics
