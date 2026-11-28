"""A mandatory Knight enters before the monarch's current creature count is used."""

from collections.abc import Mapping

from ..fixed_damage_set_model import FixedDamageSetSpec, PlayerDamageGroup, PlayerDamageRelation
from ..resolution_conditions import (
    MONARCH_KNIGHT_ENTRY_DAMAGE_VERSION, MONARCH_KNIGHT_DAMAGE_MECHANICS, MONARCH_KNIGHT_PREFIX_MECHANICS,
    validate_resolution_condition_instruction,
)
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects
from .monarch_movement_replacements import MONARCH_CONDITION, MONARCH_MOVEMENT_UPKEEP


MONARCH_KNIGHT_ENTRY_DAMAGE_CAPABILITY = "resolution.effect.monarch_knight_entry_damage"
MONARCH_KNIGHT_ENTRY_DAMAGE_MECHANICS = (
    "fixed-resolution-public-condition", *MONARCH_KNIGHT_PREFIX_MECHANICS, *MONARCH_KNIGHT_DAMAGE_MECHANICS,
)
_OPPONENTS = FixedDamageSetSpec((PlayerDamageGroup(PlayerDamageRelation.OPPONENTS),))
_KNIGHT = FrozenMap({
    "op": "create_token", "controller": "$controller", "name": "Knight Token", "quantity": 1,
    "characteristics": {"type_line": "Token Creature — Knight", "literal_token_name_version": 1,
                        "colors": ["R"], "power": "3", "toughness": "1"},
})


def knight_damage_instruction(amount):
    return {"op": "damage_fixed_set", "source": "$source", "amount": amount, **_OPPONENTS.to_dict()}


def guard_monarch_knight_damage(damage):
    return {
        "op": "apply_if_public_condition", "schema_version": MONARCH_KNIGHT_ENTRY_DAMAGE_VERSION,
        "player": "$controller", "condition": thaw_value(MONARCH_CONDITION), "effects": [damage],
        "mechanic_ids": list(MONARCH_KNIGHT_DAMAGE_MECHANICS), "prefix_mechanic_ids": list(MONARCH_KNIGHT_PREFIX_MECHANICS),
    }


def uses_monarch_knight_damage(effect):
    version = effect.get("schema_version") if isinstance(effect, Mapping) else None
    return (type(version) is int and version == MONARCH_KNIGHT_ENTRY_DAMAGE_VERSION
            and effect.get("op") == "apply_if_public_condition")


def monarch_knight_damage_capabilities(effects, target_schema):
    if (target_schema is not None or len(effects) != 2 or not isinstance(effects[0], Mapping)
            or type(effects[0].get("quantity")) is not int or FrozenMap(effects[0]) != _KNIGHT):
        raise ValueError("Ordered monarch damage requires its mandatory exact Knight before the guard")
    if not uses_monarch_knight_damage(effects[1]) or effects[1].get("player") != "$controller":
        raise ValueError("Ordered Knight damage requires the unbound resolving controller guard")
    _, damage = validate_resolution_condition_instruction(effects[1])
    from .capabilities import capability_dependencies_for_node

    token_caps = capability_dependencies_for_node(effects=effects[:1], target_schema=None, mechanic_ids=MONARCH_KNIGHT_PREFIX_MECHANICS)
    damage_caps = capability_dependencies_for_node(effects=damage, target_schema=None, mechanic_ids=MONARCH_KNIGHT_DAMAGE_MECHANICS)
    if not token_caps or not damage_caps:
        raise ValueError("Both original Knight and dynamic damage need their existing typed owners")
    return tuple(sorted({
        MONARCH_KNIGHT_ENTRY_DAMAGE_CAPABILITY, "resolution.effect.public_condition", "variant.monarch.designate",
        "trigger.effect.fixed_event", "trigger.placement.apnap", *token_caps, *damage_caps,
    }))


def monarch_knight_damage_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if uses_monarch_knight_damage(effect))
    if not marked and MONARCH_KNIGHT_ENTRY_DAMAGE_CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("step.begin", "battlefield") or program.handlers
            or program.cost_schema is not None or FrozenMap(program.event_condition or {}) != MONARCH_MOVEMENT_UPKEEP
            or len(marked) != 1 or tuple(program.effects[1:]) != marked):
        raise ValueError("Monarch Knight damage requires its complete original upkeep")
    return monarch_knight_damage_capabilities(program.effects, program.target_schema)
