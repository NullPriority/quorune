from __future__ import annotations

"""Resolve one paid source-exile roll as an ordinary owner-library insertion."""

from collections.abc import Mapping

from ..public_zone_moves import FIXED_OWNER_ZONE_MOVE_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value
from .night_shift_dice import roll_adjusted_game_die


CAPABILITY = "random.die.owner_library_depth"
MECHANIC = "single-d10-owner-library-depth"
FIELD = "single_d10_owner_library_depth"
SIDES = 10
REQUIREMENTS = (CAPABILITY, FIXED_OWNER_ZONE_MOVE_CAPABILITY, "zone.change.destination_replacement", "target.revalidate_resolution")
_CANONICAL_MANA = {"GENERIC": 5, **dict.fromkeys("WUBRGC", 0)}
_PRICE = {
    "text": "{5}, {T}, Exile this land", "mana": _CANONICAL_MANA, "complex_symbols": [],
    "tap_source": True, "untap_source": False, "discard_source": False,
    "sacrifice_source": False, "exile_source": True, "life_payment": 0, "energy_payment": 0,
    "loyalty_delta": None, "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
}


def die_library_depth_target_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"],
            "types_any": ["artifact", "creature", "planeswalker"], "count": 1}


def die_library_depth_effect():
    return {"op": "move", "card": "$target.0", "destination": "library", FIELD: 1}


def has_die_library_depth_price(cost):
    if not isinstance(cost, Mapping) or FrozenMap(cost) != FrozenMap(_PRICE):
        return False
    return (all(type(value) is int for value in cost["mana"].values())
            and all(type(cost[key]) is bool for key in (
                "tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source",
            ))
            and type(cost["life_payment"]) is int and type(cost["energy_payment"]) is int)


def die_library_depth_dependencies(*, effects, target_schema, mechanics, active_zone, cost_schema):
    if (MECHANIC not in mechanics or active_zone != "battlefield"
            or not has_die_library_depth_price(cost_schema) or len(effects) != 1
            or type(effects[0].get(FIELD)) is not int
            or FrozenMap(effects[0]) != FrozenMap(die_library_depth_effect())
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != FrozenMap(die_library_depth_target_schema())):
        return ()
    return REQUIREMENTS


def die_library_depth_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key

    if CAPABILITY not in program.capability_dependencies and not contains_nested_key(
        (program.effects, program.target_schema, program.handlers), FIELD,
    ):
        return ()
    required = die_library_depth_dependencies(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        active_zone=program.active_zone, cost_schema=program.cost_schema,
    )
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    if (not required or program.event != "activate" or program.event_condition is not None
            or program.trigger_limit is not None or len(program.handlers) != 1):
        raise ValueError("The die insertion requires its exact original paid activation")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (ability.builtin_semantic_key != program.key or ability.mana_ability or not ability.sorcery_speed
            or ability.activation_conditions or ability.activation_limit is not None
            or ability.zones != ("battlefield",) or activated_ability_cost(ability) != thaw_value(program.cost_schema)
            or ability.target_schema is not None
            and FrozenMap(ability.target_schema) != FrozenMap(die_library_depth_target_schema())):
        raise ValueError("The die insertion changed its original sorcery timing, price, or target")
    return required


def is_closed_die_library_depth_program(program):
    if CAPABILITY not in program.capability_dependencies:
        return False
    try:
        required = die_library_depth_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bind_die_library_depth(host, item, effect, *, instruction_pointer):
    if (type(effect.get(FIELD)) is not int or FrozenMap(effect) != FrozenMap(die_library_depth_effect())
            or item.kind != "activated_ability" or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or len(item.targets) != 1
            or type(item.targets[0]) is not str or not item.targets[0]):
        raise ValueError("The die insertion requires its original resolving activated target")
    result = roll_adjusted_game_die(host, item, sides=SIDES, instruction_pointer=instruction_pointer).single_result
    return {"op": "move", "card": item.targets[0], "destination": "library", "position": max(0, result) + 1}
