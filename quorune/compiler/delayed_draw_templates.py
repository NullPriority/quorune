from __future__ import annotations

"""Closed compiler grammar for one fixed next-turn upkeep draw."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping


FIXED_NEXT_TURN_DRAW_MECHANIC = "fixed-next-turn-upkeep-draw"
FIXED_NEXT_TURN_DRAW_CAPABILITY = "trigger.delayed.fixed_next_turn_draw"
FIXED_NEXT_TURN_DRAW_TEMPLATE = "draw-controller-next-turn-upkeep-v1"
_LABEL = "Draw at the beginning of the next turn's upkeep"
_FIXED_NEXT_TURN_DRAW = re.compile(
    r"Draw a card at the beginning of the next turn's upkeep\.?",
    re.IGNORECASE,
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
