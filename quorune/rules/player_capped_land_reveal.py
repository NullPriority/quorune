"""Bind announced library reveal X to the players present during the cast."""

from ..replacement.immutable import FrozenMap
from ..reveal_until_library_selection_model import (
    PLAYER_CAPPED_LAND_REVEAL_CAPABILITY, PLAYER_CAPPED_LAND_REVEAL_VERSION,
    REVEAL_UNTIL_OPERATION, player_capped_land_reveal_effect, reveal_until_selection_payload_dependencies,
)
from .effect_shape_walkers import nested_effects


def marks_player_capped_land_reveal(effect):
    return (effect.get("op") == REVEAL_UNTIL_OPERATION
            and effect.get("schema_version") == PLAYER_CAPPED_LAND_REVEAL_VERSION)


def _exact_player_capped_land_reveal(program):
    return ((program.event, program.active_zone) == ("resolve", "stack")
            and len(program.effects) == 1
            and type(program.effects[0].get("schema_version")) is int
            and FrozenMap(program.effects[0]) == FrozenMap(player_capped_land_reveal_effect())
            and program.target_schema is None and program.event_condition is None
            and program.cost_schema is None and not program.handlers and program.trigger_limit is None)


def player_capped_land_reveal_runtime_dependencies(program):
    values = (program.effects, program.handlers, program.target_schema, program.cost_schema)
    if (not any(marks_player_capped_land_reveal(effect) for effect in nested_effects(values))
            and PLAYER_CAPPED_LAND_REVEAL_CAPABILITY not in program.capability_dependencies):
        return ()
    if not _exact_player_capped_land_reveal(program):
        raise ValueError("Player-capped land reveal requires the exact spell and its announced X")
    return reveal_until_selection_payload_dependencies(program.effects[0])


def source_limited_cast_x(host, program, static_maximum):
    if (not any(marks_player_capped_land_reveal(effect) for effect in getattr(program, 'effects', ()))
            or not _exact_player_capped_land_reveal(program)):
        return static_maximum
    maximum = len(host.active_seats)
    return maximum if static_maximum is None else min(static_maximum, maximum)
