"""Admission binds the full instruction to its original self-damage event."""

from collections.abc import Mapping

from ..combat_damage_library_exile import (
    COMBAT_DAMAGE_CAST_EXILE_CAPABILITY, COMBAT_DAMAGE_EXILE_CAPABILITY,
    is_combat_damage_cast_exile, is_combat_damage_exile,
    validate_combat_damage_cast_exile, validate_combat_damage_exile,
)
from ..compiler.impulse_access_templates import IMPULSE_ACCESS_CAPABILITY_ID, IMPULSE_ACCESS_MECHANIC_ID
from ..compiler.fixed_public_event_trigger_bindings import source_damage_incarnation_condition
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


COMBAT_DAMAGE_LIBRARY_CONDITION = FrozenMap({"all": (
    {"field": "target_kind", "op": "eq", "value": "player"},
    {"field": "combat", "op": "truthy", "value": True},
    source_damage_incarnation_condition(),
)})
COMBAT_DAMAGE_LIBRARY_DEPENDENCIES = (
    COMBAT_DAMAGE_EXILE_CAPABILITY, IMPULSE_ACCESS_CAPABILITY_ID,
    "trigger.event.normalized_damage", "trigger.effect.fixed_event", "trigger.placement.apnap",
    "zone.change.destination_replacement",
)


COMBAT_DAMAGE_CAST_LIBRARY_DEPENDENCIES = (
    COMBAT_DAMAGE_CAST_EXILE_CAPABILITY, *COMBAT_DAMAGE_LIBRARY_DEPENDENCIES[1:],
)


def combat_damage_treasure_cast_node_capabilities(*, effects, target_schema, mechanic_ids):
    token_mechanic = "cr-111-tokens"
    if (len(effects) != 2 or target_schema is not None
            or not {IMPULSE_ACCESS_MECHANIC_ID, token_mechanic} <= set(mechanic_ids)):
        return ()
    treasure, exile = effects
    characteristics = treasure.get("characteristics")
    if (treasure.get("op") != "create_token" or treasure.get("controller") != "$controller"
            or treasure.get("name") != "Treasure Token" or type(treasure.get("quantity")) is not int
            or treasure["quantity"] != 1 or "tapped" in treasure
            or not isinstance(characteristics, Mapping)
            or characteristics.get("type_line") != "Token Artifact — Treasure"
            or characteristics.get("activated_ability_profile") != "tap_sac_any_color_mana_v1"):
        return ()
    from .token_creation_capability_shapes import fixed_token_creation_node_capabilities

    token = fixed_token_creation_node_capabilities(
        effects=(treasure,), target_schema=None, mechanic_ids={token_mechanic},
    )
    if not token:
        return ()
    try:
        validate_combat_damage_cast_exile(exile)
    except ValueError:
        return ()
    return tuple(sorted({*token, *COMBAT_DAMAGE_CAST_LIBRARY_DEPENDENCIES}))


def combat_damage_library_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None or IMPULSE_ACCESS_MECHANIC_ID not in mechanic_ids:
        return ()
    try:
        validate_combat_damage_exile(effects[0])
    except ValueError:
        return ()
    return COMBAT_DAMAGE_LIBRARY_DEPENDENCIES


def combat_damage_library_program_dependencies(program):
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers)) if is_combat_damage_exile(effect))
    cast_marked = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                        if is_combat_damage_cast_exile(effect))
    cast_access = bool(cast_marked or COMBAT_DAMAGE_CAST_EXILE_CAPABILITY in program.capability_dependencies)
    if not marked and not cast_access and COMBAT_DAMAGE_EXILE_CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("damage.dealt.self", "battlefield")
            or program.cost_schema is not None or program.handlers
            or FrozenMap(program.event_condition or {}) != COMBAT_DAMAGE_LIBRARY_CONDITION
            or (bool(marked) or COMBAT_DAMAGE_EXILE_CAPABILITY in program.capability_dependencies
                if cast_access else len(marked) != 1 or effects != marked)):
        raise ValueError("Combat-damage impulse access requires the complete original self combat hit to a player")
    resolver = combat_damage_treasure_cast_node_capabilities if cast_access else combat_damage_library_node_capabilities
    required = resolver(effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
    if not required:
        raise ValueError("Combat-damage impulse access lost its untargeted supplier or play policy")
    return required
