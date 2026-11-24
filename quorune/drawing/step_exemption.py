"""Observe the first completed draw in the affected player’s own draw step."""

from __future__ import annotations

from collections.abc import Mapping


DRAW_STEP_EXEMPT_MULTIPLIER_HANDLER_ID = "replacement.draw.result.multiply_after_first.v1"
DRAW_STEP_EXEMPT_MULTIPLIER_CAPABILITY = "zone.draw.result_multiplier_after_first"
DRAW_STEP_EXEMPT_REDIRECT_HANDLER_ID = "replacement.draw.result.redirect_after_first.v1"
DRAW_STEP_EXEMPT_REDIRECT_CAPABILITY = "zone.draw.result_redirect_after_first"
DRAW_STEP_EXEMPT_DISCARD_HANDLER_ID = "replacement.draw.discard_or_mill_after_first.v1"
DRAW_STEP_EXEMPT_DISCARD_CAPABILITY = "zone.draw.discard_replacement_after_first"


def is_first_draw_in_own_draw_step(state, player: str) -> bool:
    if state.active_player != player or (state.phase, state.step) != ("beginning", "draw"):
        return False
    extra = getattr(state, "additional_beginning_phases", None)
    if extra is not None and extra.active:
        return extra.draws_completed == 0
    recorded = state.players[player].stats.get("cards_drawn_in_draw_step_by_turn", {})
    if not isinstance(recorded, Mapping):
        raise ValueError("Draw-step totals require their historical count mapping")
    count = recorded.get(str(state.turn_sequence), 0)
    if type(count) is not int or count < 0:
        raise ValueError("Draw-step totals require an actual nonnegative draw count")
    return count == 0
