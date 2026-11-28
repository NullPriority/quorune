from __future__ import annotations

"""Closed compiler grammar for one fixed next-turn upkeep draw."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping
from ..turn_creature_cast_draw_model import turn_creature_cast_draw_effect


def watched_combat_damage_draw_template(text):
    from ..rules.watched_combat_draw import (
        WATCHED_COMBAT_DRAW_MECHANIC, WATCHED_COMBAT_DRAW_TARGET, watched_combat_draw_effect,
    )

    if re.fullmatch(
        r"Choose target creature you control\. Whenever that creature deals combat damage "
        r"to a player or planeswalker this turn, draw that many cards\.",
        " ".join(text.split()), re.I,
    ) is None:
        return None
    return ("watched-combat-damage-draw-this-turn-v1", (watched_combat_draw_effect(),),
            WATCHED_COMBAT_DRAW_TARGET, (WATCHED_COMBAT_DRAW_MECHANIC,))


def controlled_player_damage_token_template(text):
    from ..rules.controlled_combat_tokens import COMBAT_TOKEN_MECHANIC, COMBAT_TOKEN_TARGET, combat_token_effect
    from .token_templates import fixed_token_creation_effect_template

    if re.fullmatch(
        r"Choose target opponent\. Whenever a creature you control deals combat damage "
        r"to that player this turn, create a tapped Treasure token\.",
        " ".join(text.split()), re.I,
    ) is None:
        return None
    token = fixed_token_creation_effect_template("Create a tapped Treasure token.").effect
    return ("controlled-opponent-combat-token-this-turn-v1", (combat_token_effect(token),),
            COMBAT_TOKEN_TARGET, (COMBAT_TOKEN_MECHANIC, "cr-115-targets"))


FIXED_NEXT_TURN_DRAW_MECHANIC = "fixed-next-turn-upkeep-draw"
FIXED_NEXT_TURN_DRAW_CAPABILITY = "trigger.delayed.fixed_next_turn_draw"
FIXED_NEXT_TURN_DRAW_TEMPLATE = "draw-controller-next-turn-upkeep-v1"
_LABEL = "Draw at the beginning of the next turn's upkeep"
_FIXED_NEXT_TURN_DRAW = re.compile(
    r"Draw a card at the beginning of the next turn's upkeep\.?",
    re.IGNORECASE,
)
_SAME_TURN_CREATURE_CAST_DRAW = re.compile(
    r"Whenever you cast a creature spell this turn, draw a card\.", re.IGNORECASE,
)


def turn_creature_cast_draw_template(text):
    if _SAME_TURN_CREATURE_CAST_DRAW.fullmatch(" ".join(text.split())) is None:
        return None
    return (
        "same-turn-creature-cast-draw-v1",
        (turn_creature_cast_draw_effect(),),
        None,
        ("cr-121-drawing-a-card", "cr-603-handling-triggered-abilities"),
    )


def fixed_next_turn_upkeep_draw_effect() -> dict[str, Any]:
    """Return the canonical immutable delayed-trigger payload."""

    return {
        "op": "delayed_trigger",
        "controller": "$controller",
        "label": _LABEL,
        "event": "step.begin",
        "condition": {
            "phase": "beginning",
            "step": "upkeep",
            "next_turn_after_sequence": "$turn_sequence",
        },
        "stack": {
            "label": _LABEL,
            "context": {
                "dynamic_effects": [
                    {
                        "op": "draw",
                        "player": "$controller",
                        "count": 1,
                        "private": True,
                    }
                ]
            },
        },
        "once": True,
    }


@dataclass(frozen=True, slots=True)
class FixedNextTurnDrawTemplate:
    template_id: str = FIXED_NEXT_TURN_DRAW_TEMPLATE

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            (fixed_next_turn_upkeep_draw_effect(),),
            None,
            (
                FIXED_NEXT_TURN_DRAW_MECHANIC,
                "cr-121-drawing-a-card",
                "cr-603-handling-triggered-abilities",
            ),
        )


def fixed_next_turn_upkeep_draw_effect_template(
    text: str,
) -> FixedNextTurnDrawTemplate | None:
    """Lower only the complete mandatory one-card delayed draw sentence."""

    if _FIXED_NEXT_TURN_DRAW.fullmatch(" ".join(text.split())) is None:
        return None
    return FixedNextTurnDrawTemplate()


__all__ = [
    "FIXED_NEXT_TURN_DRAW_CAPABILITY",
    "FIXED_NEXT_TURN_DRAW_MECHANIC",
    "FIXED_NEXT_TURN_DRAW_TEMPLATE",
    "FixedNextTurnDrawTemplate",
    "fixed_next_turn_upkeep_draw_effect",
    "fixed_next_turn_upkeep_draw_effect_template",
]
