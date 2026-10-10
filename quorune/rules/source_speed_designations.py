from __future__ import annotations

"""Materialize source-local designations and current controller speed together."""

from ..permanent_designations import permanent_numeric_designations
from .player_speed_model import player_speed


def current_source_designations(state,card):
    player=state.players.get(card.controller)
    return {**permanent_numeric_designations(card),'controller_speed':player_speed(player) if player is not None else None}
