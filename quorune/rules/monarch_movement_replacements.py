"""Two exact movement branches selected through the existing monarch condition."""

from collections.abc import Mapping

from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


MONARCH_MOVEMENT_CAPABILITY = "resolution.effect.monarch_movement_replacement"
MONARCH_MOVEMENT_TEMPLATE = "monarch-movement-replacement-v1"
MONARCH_CONDITION = FrozenMap(FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH).to_dict())
MONARCH_MOVEMENT_UPKEEP = FrozenMap({"all": [
    {"field": "player", "op": "eq", "value": "$source.controller"},
    {"field": "step", "op": "eq", "value": "upkeep"},
]})
_GRAVEYARD_TARGET = FrozenMap({
    "zones": ["graveyard"], "categories": ["card"], "owner_relation": "you", "count": 1,
    "types_any": ["artifact", "battle", "creature", "enchantment", "land", "planeswalker"], "mana_value_max": 3,
})


def monarch_movement_target_schema(mode):
    return thaw_value(_GRAVEYARD_TARGET) if mode == "graveyard_destination" else None


def _hand_entry(query):
    return {"op": "put_card_from_hand", "player": "$controller", "query": query, "tapped": False}


def monarch_movement_effect(mode):
    if mode == "hand_entry":
        from ..hand_entry_queries import creature_or_land_hand_entry_query_descriptor

        normal = _hand_entry(ObjectQuerySpec(zones=("hand",), types_all=("land",)).canonical_dict())
        royal = _hand_entry(creature_or_land_hand_entry_query_descriptor())
        normal_mechanics = royal_mechanics = ("fixed-private-hand-entry", "fixed-public-zone-move")
        version = 3
    elif mode == "graveyard_destination":
        normal = {"op": "return_graveyard_card_to_owner_hand", "card": "$target.0"}
        royal = {"op": "reanimate", "card": "$target.0", "controller": "$controller", "tapped": False}
        normal_mechanics = ("return-to-owner-hand", "cr-115-targets")
        royal_mechanics = ("fixed-target-reanimation", "cr-115-targets")
        version = 8
    else:
        raise ValueError("Unknown monarch movement replacement")
    result = {
        "op": "apply_if_public_condition", "schema_version": version, "player": "$controller",
        "condition": thaw_value(MONARCH_CONDITION), "effects": [royal], "mechanic_ids": list(royal_mechanics),
        "otherwise_effects": [normal], "otherwise_mechanic_ids": list(normal_mechanics), "prefix_mechanic_ids": [],
    }
    if mode == "graveyard_destination":
        result["target_binding"] = thaw_value(_GRAVEYARD_TARGET)
    return result


def monarch_movement_mode(effect):
    if (not isinstance(effect, Mapping) or effect.get("op") != "apply_if_public_condition"
            or type(effect.get("schema_version")) is not int or effect.get("schema_version") not in (3, 8)
            or not isinstance(effect.get("condition"), Mapping)
            or FrozenMap(effect["condition"]) != MONARCH_CONDITION):
        return None
    children = effect.get("effects")
    if not isinstance(children, (list, tuple)) or len(children) != 1 or not isinstance(children[0], Mapping):
        return None
    royal_op = children[0].get("op")
    if royal_op == "put_card_from_hand":
        mode = "hand_entry"
    elif royal_op == "reanimate":
        mode = "graveyard_destination"
    else:
        return None
    return mode if FrozenMap(effect) == FrozenMap(monarch_movement_effect(mode)) else None


def monarch_movement_capabilities(effect, target_schema):
    mode = monarch_movement_mode(effect)
    if mode is None:
        raise ValueError("Monarch movement requires both complete printed branches")
    target = monarch_movement_target_schema(mode)
    if target is None:
        if target_schema is not None:
            raise ValueError("Private hand deployment has no target")
    elif (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or type(target_schema.get("mana_value_max")) is not int
            or FrozenMap(target_schema) != _GRAVEYARD_TARGET):
        raise ValueError("Monarch graveyard movement requires the original target for both destinations")
    from ..resolution_conditions import validate_resolution_condition_instruction
    from .capabilities import capability_dependencies_for_node

    validate_resolution_condition_instruction(effect)
    required = {
        MONARCH_MOVEMENT_CAPABILITY, "variant.monarch.designate", "resolution.effect.public_condition",
        "resolution.effect.fixed_public_self_replacement", "trigger.effect.fixed_event", "trigger.placement.apnap",
    }
    if target is not None:
        required.add("resolution.effect.public_same_target_self_replacement")
    for branch, mechanics in (("effects", "mechanic_ids"), ("otherwise_effects", "otherwise_mechanic_ids")):
        owned = capability_dependencies_for_node(
            effects=effect[branch], target_schema=target_schema, mechanic_ids=effect[mechanics],
        )
        if not owned:
            raise ValueError("Both printed monarch movement branches need their stock owner")
        required.update(owned)
    return tuple(sorted(required))


def monarch_movement_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if monarch_movement_mode(effect) is not None)
    if not marked and MONARCH_MOVEMENT_CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or not isinstance(program.event_condition, Mapping)
            or FrozenMap(program.event_condition) != MONARCH_MOVEMENT_UPKEEP
            or program.handlers or program.cost_schema is not None
            or len(marked) != 1 or tuple(program.effects) != marked):
        raise ValueError("Monarch movement requires the complete original controller upkeep")
    return monarch_movement_capabilities(marked[0], program.target_schema)
