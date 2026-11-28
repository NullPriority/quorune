"""Complete original source attack admission for defending-player top exile."""

from collections.abc import Mapping

from ..compiler.impulse_access_templates import IMPULSE_ACCESS_CAPABILITY_ID, IMPULSE_ACCESS_MECHANIC_ID
from ..defending_player_library_exile import (
    DEFENDING_PLAYER_EXILE_CAPABILITY, is_defending_player_exile, validate_defending_player_exile,
)
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


DEFENDING_PLAYER_TOP_ATTACK_CONDITION = FrozenMap({"field": "card", "op": "eq", "value": "$source.ref"})


def defending_player_top_exile_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or target_schema is not None
            or IMPULSE_ACCESS_MECHANIC_ID not in mechanic_ids or not is_defending_player_exile(effects[0])):
        return ()
    try:
        validate_defending_player_exile(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    return (DEFENDING_PLAYER_EXILE_CAPABILITY, IMPULSE_ACCESS_CAPABILITY_ID,
            "trigger.event.normalized_self_attack", "trigger.placement.apnap", "trigger.effect.fixed_event",
            "zone.change.destination_replacement")


def defending_player_top_exile_program_dependencies(program):
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers)) if is_defending_player_exile(effect))
    if not marked and DEFENDING_PLAYER_EXILE_CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or program.handlers or program.cost_schema is not None or len(marked) != 1 or effects != marked
            or not isinstance(program.event_condition, Mapping)
            or FrozenMap(program.event_condition) != DEFENDING_PLAYER_TOP_ATTACK_CONDITION):
        raise ValueError("Defending-player exile requires the complete original untargeted self-attack")
    required = defending_player_top_exile_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Defending-player exile lost the original supplying attack")
    return required
