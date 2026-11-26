"""Close the ordered public exile and private draw for an opposing land entry."""

from collections.abc import Mapping

from ..land_entry_library_exile_model import (
    LAND_ENTRY_TOP_EXILE_CAPABILITY,
    is_land_entry_top_exile,
    validate_land_entry_top_exile,
)
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


OPPONENT_LAND_ENTRY_CONDITION = FrozenMap({
    "field": "controller", "op": "ne", "value": "$source.controller",
})
_CONTROLLER_DRAW = FrozenMap({"op": "draw", "player": "$controller", "count": 2, "private": True})
_EFFECT_CAPABILITIES = (
    LAND_ENTRY_TOP_EXILE_CAPABILITY,
    "zone.impulse_access.fixed",
    "zone.draw.library_to_hand",
    "zone.change.destination_replacement",
    "trigger.event.normalized_zone_change",
    "trigger.placement.apnap",
    "trigger.effect.fixed_event",
)


def land_entry_top_exile_node_capabilities(*, effects, target_schema):
    if len(effects) != 2 or target_schema is not None:
        return ()
    exile, draw = effects
    if (not isinstance(draw, Mapping) or FrozenMap(draw) != _CONTROLLER_DRAW
            or type(draw["count"]) is not int or draw["private"] is not True):
        return ()
    try:
        validate_land_entry_top_exile(exile)
    except ValueError:
        return ()
    return _EFFECT_CAPABILITIES


def land_entry_top_exile_program_dependencies(program):
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                   if is_land_entry_top_exile(effect))
    if not marked and LAND_ENTRY_TOP_EXILE_CAPABILITY not in program.capability_dependencies:
        return ()
    required = land_entry_top_exile_node_capabilities(
        effects=effects, target_schema=program.target_schema,
    )
    if (not required or (program.event, program.active_zone) != ("land.enter", "battlefield")
            or program.cost_schema is not None or program.handlers or program.trigger_limit is not None
            or not isinstance(program.event_condition, Mapping)
            or FrozenMap(program.event_condition) != OPPONENT_LAND_ENTRY_CONDITION):
        raise ValueError("Land-entry top exile requires its complete original untargeted trigger")
    return required
