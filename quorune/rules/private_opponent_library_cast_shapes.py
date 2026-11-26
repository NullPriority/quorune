from __future__ import annotations

"""Bind a private linked library result to its source entry and one opponent."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


_PRIVATE_OPPONENT_LIBRARY_CAST_DEPENDENCIES = (
    "library.select.fixed_controller",
    "mana.spending.as_though",
    "target.revalidate_resolution",
    "trigger.event.normalized_zone_change",
    "trigger.placement.apnap",
    "zone.change.destination_replacement",
    "zone.impulse_access.fixed",
)


def damage_library_cast_condition():
    return {"all": [
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
        {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
    ]}


def _damage_library_cast_requirements():
    from ..private_opponent_library_cast_model import DAMAGE_LIBRARY_CAST_CAPABILITY

    return (DAMAGE_LIBRARY_CAST_CAPABILITY, "library.select.fixed_controller", "mana.spending.as_though",
            "trigger.event.normalized_damage", "trigger.placement.apnap", "zone.change.destination_replacement",
            "zone.impulse_access.fixed", "zone.graveyard.ordered_simultaneous_arrival")


def private_opponent_library_cast_target_schema():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}


def private_opponent_library_cast_payload_dependencies(effect):
    from ..private_opponent_library_cast_model import (
        PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY, uses_private_opponent_library_cast_selection,
        validate_private_opponent_library_cast_selection, uses_damage_library_cast_selection,
    )
    if not uses_private_opponent_library_cast_selection(effect):
        return ()
    validate_private_opponent_library_cast_selection(effect)
    if uses_damage_library_cast_selection(effect):
        return _damage_library_cast_requirements()
    return (PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY, *_PRIVATE_OPPONENT_LIBRARY_CAST_DEPENDENCIES)


def private_opponent_library_cast_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..private_opponent_library_cast_model import DAMAGE_LIBRARY_CAST_MECHANIC, PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC, uses_damage_library_cast_selection

    if len(effects) == 1 and uses_damage_library_cast_selection(effects[0]):
        if target_schema is not None or DAMAGE_LIBRARY_CAST_MECHANIC not in mechanic_ids:
            return ()
        try:
            return private_opponent_library_cast_payload_dependencies(effects[0])
        except (KeyError, TypeError, ValueError):
            return ()
    if (
        PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC not in mechanic_ids or len(effects) != 1
        or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
        or FrozenMap(target_schema) != FrozenMap(private_opponent_library_cast_target_schema())
    ):
        return ()
    try:
        return private_opponent_library_cast_payload_dependencies(effects[0])
    except (KeyError, TypeError, ValueError):
        return ()


def private_opponent_library_cast_program_dependencies(program):
    from ..private_opponent_library_cast_model import (
        DAMAGE_LIBRARY_CAST_CAPABILITY, DAMAGE_LIBRARY_CAST_MECHANIC,
        PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY, PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC,
        uses_private_opponent_library_cast_selection, uses_damage_library_cast_selection,
    )

    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_private_opponent_library_cast_selection(effect))
    if (any(uses_damage_library_cast_selection(effect) for effect in marked)
            or DAMAGE_LIBRARY_CAST_CAPABILITY in program.capability_dependencies or DAMAGE_LIBRARY_CAST_MECHANIC in program.coverage):
        if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers
                or (program.event, program.active_zone) != ("damage.dealt.self", "battlefield")
                or FrozenMap(program.event_condition or {}) != FrozenMap(damage_library_cast_condition())
                or program.target_schema is not None or program.cost_schema is not None or program.trigger_limit is not None):
            raise ValueError("A private top-three cast requires its original source damaging a player in combat.")
        return private_opponent_library_cast_payload_dependencies(marked[0])
    if (not marked and PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY not in program.capability_dependencies
            and PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC not in program.coverage):
        return ()
    if (
        len(marked) != 1 or tuple(program.effects) != marked or program.handlers
        or program.event != "permanent.enter.self" or program.active_zone != "battlefield"
        or program.event_condition is not None or program.cost_schema is not None
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("Private opponent library cast requires its current source entry and one closed effect")
    required = private_opponent_library_cast_node_capabilities(
        effects=marked, target_schema=program.target_schema, mechanic_ids=(PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC,),
    )
    if not required:
        raise ValueError("Private opponent library cast requires its original opponent target and linked result")
    return required
