"""Monarch replacement of a fixed mill over the original selected players."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from ..target_cardinality import AnnouncedTargetCardinality, SELECTED_PLAYER_RESULT_MECHANIC
from .any_number_target_shapes import selected_player_result_node_capabilities
from .effect_shape_walkers import nested_effects
from .monarch_movement_replacements import MONARCH_CONDITION, MONARCH_MOVEMENT_UPKEEP


MONARCH_SELECTED_PLAYER_MILL_CAPABILITY = "resolution.effect.monarch_selected_player_mill"
_TARGET_SCHEMA = FrozenMap({
    "zones": ["player"], "categories": ["player"], "player_relation": "any",
    "count_parameter": AnnouncedTargetCardinality(
        selection="up_to", parameter="any_number", schema_version=2,
    ).to_dict(),
})
_MILL_MECHANICS = ("any-number-target-set", SELECTED_PLAYER_RESULT_MECHANIC, "cr-115-targets", "mill")


def monarch_selected_player_mill_schema():
    return thaw_value(_TARGET_SCHEMA)


def monarch_selected_player_mill_effect():
    return {
        "op": "apply_if_public_condition", "schema_version": 3, "player": "$controller",
        "condition": thaw_value(MONARCH_CONDITION), "prefix_mechanic_ids": [],
        "effects": [{"op": "mill", "players": "$targets", "count": 10}],
        "mechanic_ids": list(_MILL_MECHANICS),
        "otherwise_effects": [{"op": "mill", "players": "$targets", "count": 2}],
        "otherwise_mechanic_ids": list(_MILL_MECHANICS),
    }


def uses_monarch_selected_player_mill(effect):
    if (not isinstance(effect, Mapping) or effect.get("op") != "apply_if_public_condition"
            or type(effect.get("schema_version")) is not int or effect["schema_version"] != 3
            or not isinstance(effect.get("condition"), Mapping) or FrozenMap(effect["condition"]) != MONARCH_CONDITION):
        return False
    branches = (effect.get("effects"), effect.get("otherwise_effects"))
    return all(isinstance(branch, (list, tuple)) and any(
        isinstance(child, Mapping) and child.get("op") == "mill" and child.get("players") == "$targets"
        for child in branch
    ) for branch in branches)


def monarch_selected_player_mill_capabilities(effect, target_schema):
    if (not uses_monarch_selected_player_mill(effect) or FrozenMap(effect) != FrozenMap(monarch_selected_player_mill_effect())
            or any(type(effect[branch][0]["count"]) is not int for branch in ("effects", "otherwise_effects"))
            or not isinstance(target_schema, Mapping) or FrozenMap(target_schema) != _TARGET_SCHEMA):
        raise ValueError("Monarch mill requires both printed counts over one original unrestricted player set")
    from ..resolution_conditions import validate_resolution_condition_instruction

    validate_resolution_condition_instruction(effect)
    required = {
        MONARCH_SELECTED_PLAYER_MILL_CAPABILITY, "resolution.effect.public_condition",
        "resolution.effect.fixed_public_self_replacement", "variant.monarch.designate",
        "trigger.effect.fixed_event", "trigger.placement.apnap",
    }
    for branch in ("effects", "otherwise_effects"):
        owned = selected_player_result_node_capabilities(
            effects=effect[branch], target_schema=monarch_selected_player_mill_schema(), mechanic_ids=_MILL_MECHANICS,
        )
        if not owned:
            raise ValueError("The original selected-player set must use the stock mill and target owners")
        required.update(owned)
    return tuple(sorted(required))


def monarch_selected_player_mill_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if uses_monarch_selected_player_mill(effect))
    if not marked and MONARCH_SELECTED_PLAYER_MILL_CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or FrozenMap(program.event_condition or {}) != MONARCH_MOVEMENT_UPKEEP
            or len(marked) != 1 or tuple(program.effects) != marked or program.handlers or program.cost_schema is not None):
        raise ValueError("Monarch mill requires one complete original controller upkeep")
    return monarch_selected_player_mill_capabilities(marked[0], program.target_schema)
