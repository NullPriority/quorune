"""Reuse the canonical sacrifice shape and the existing sealed-player contract."""
from __future__ import annotations

from collections.abc import Mapping

from ..compiler.event_player_choice_templates import EVENT_PLAYER_CHOICE_CAPABILITY, EVENT_PLAYER_CHOICE_MECHANIC
from .affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
from .event_player_result_shapes import _event_player_reference_is_closed


_PLAYERS = frozenset({'$context.player', '$context.target', '$context.defending_player',
                      '$context.previous_controller', '$context.controller'})


def event_player_sacrifice_node_capabilities(*, effects, target_schema, mechanic_ids):
    if EVENT_PLAYER_CHOICE_MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:
        return ()
    effect = effects[0]
    if not isinstance(effect, Mapping):
        return ()
    players = effect.get('players')
    if (not isinstance(players, (list, tuple)) or len(players) != 1
            or type(players[0]) is not str or players[0] not in _PLAYERS):
        return ()
    # The choice retains the ordinary spell/ability controller as its origin;
    # its existing player list independently selects the affected chooser.
    normalized = {**effect, 'players': ['$controller']}
    dependencies = fixed_affected_player_sacrifice_node_capabilities(
        effects=(normalized,), target_schema=None, mechanic_ids=mechanic_ids)
    return tuple(sorted({EVENT_PLAYER_CHOICE_CAPABILITY, *dependencies})) if dependencies else ()


def is_closed_event_player_sacrifice_program(program):
    required = event_player_sacrifice_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies) and all(
        _event_player_reference_is_closed(program, effect['players'][0]) for effect in program.effects)
