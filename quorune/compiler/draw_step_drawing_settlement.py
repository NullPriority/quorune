from __future__ import annotations

"""Recognize the complete optional draw and drawn-card settlement trigger."""

from ..rules.draw_step_drawing_settlement import (
    DRAW_STEP_SETTLEMENT_MECHANIC,
    controller_draw_step_condition,
    draw_step_settlement_effect,
)

DRAW_STEP_SETTLEMENT_TEMPLATE = "draw-step-drawn-card-settlement-v1"
_BODY = (
    "you may draw two additional cards. if you do, choose two cards in your hand "
    "drawn this turn. for each of those cards, pay 4 life or put the card on top "
    "of your library."
)


def draw_step_settlement_trigger_result(binding, body):
    if (
        (binding.event.value, binding.active_zone, binding.variant)
        != ("step.begin", "battlefield", "your draw step")
        or binding.event_condition != controller_draw_step_condition()
        or body != binding.body
        or " ".join(body.casefold().split()) != _BODY
    ):
        return None
    return (
        DRAW_STEP_SETTLEMENT_TEMPLATE, (draw_step_settlement_effect(),), None,
        (DRAW_STEP_SETTLEMENT_MECHANIC,),
    )
