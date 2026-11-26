from __future__ import annotations

"""Close an ordered draw and life-loss result over one owned commander."""

from collections.abc import Mapping

from .effect_shape_walkers import nested_effects


OWNED_COMMANDER_AMOUNT_FIELD = "owned_commander_mana_value"
OWNED_COMMANDER_AMOUNT_MECHANIC = "owned-commander-mana-value"
OWNED_COMMANDER_AMOUNT_CAPABILITY = "quantity_expression.owned_commander_mana_value"


def owned_commander_amount_effect(player="$controller"):
    return {"op": "choose_objects", "player": player, OWNED_COMMANDER_AMOUNT_FIELD: 1}


def is_owned_commander_amount_effect(effect, *, player="$controller"):
    return (isinstance(effect, Mapping)
            and type(effect.get(OWNED_COMMANDER_AMOUNT_FIELD)) is int
            and dict(effect) == owned_commander_amount_effect(player))


def owned_commander_amount_node_capabilities(*, effects, target_schema, mechanic_ids, **_unused):
    if (OWNED_COMMANDER_AMOUNT_MECHANIC in mechanic_ids and target_schema is None and len(effects) == 1
            and is_owned_commander_amount_effect(effects[0])):
        return (OWNED_COMMANDER_AMOUNT_CAPABILITY,)
    return ()


def owned_commander_amount_runtime_dependencies(program):
    if not any(OWNED_COMMANDER_AMOUNT_FIELD in effect for effect in nested_effects(
        (program.effects, program.handlers),
    )):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")
            or program.target_schema is not None or program.event_condition is not None
            or program.cost_schema is not None or program.handlers
            or len(program.effects) != 1 or not is_owned_commander_amount_effect(program.effects[0])):
        raise ValueError("Owned commander mana value requires its exact nontargeted spell instruction")
    return (OWNED_COMMANDER_AMOUNT_CAPABILITY,)


def is_closed_owned_commander_amount_program(program):
    try:
        required = owned_commander_amount_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
