"""Complete owned prefixes and results for fixed ability-resolution ordinals."""

from collections.abc import Mapping

from ..ability_resolution_ordinal import (
    is_second_ability_draw, is_third_ability_result, ordinal_controller_creatures,
    validate_second_ability_draw, validate_third_ability_result,
)
from ..replacement.immutable import FrozenMap, thaw_value


_ORDINAL_CAPABILITIES = {"resolution.effect.public_condition", "condition.history.fixed_public_query"}
_LIFE = {"op": "life", "player": "$controller", "delta": 1}
_DAMAGE = {"op": "damage_each_opponent", "source": "$source", "amount": 1}
_BUFF = {"op": "modify_stats_until_end_of_turn", "card": "$target.0", "power": 1, "toughness": 0}
_CREATURE_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "types_any": ["creature"], "controller_relation": "you",
}


def _prefix_event(effects, target_schema):
    if len(effects) != 2:
        return None
    prefix = thaw_value(FrozenMap(effects[0]))
    if target_schema is None:
        if prefix == _LIFE and type(prefix["delta"]) is int:
            return "permanent.enter"
        if prefix == _DAMAGE and type(prefix["amount"]) is int:
            return "land.enter"
    if (isinstance(target_schema, Mapping) and type(target_schema.get("count")) is int
            and thaw_value(FrozenMap(target_schema)) == _CREATURE_TARGET
            and prefix == _BUFF and type(prefix["power"]) is int and type(prefix["toughness"]) is int):
        return "permanent.enter"
    return None


def second_ability_draw_node_capabilities(*, effects, target_schema, mechanic_ids):
    if _prefix_event(effects, target_schema) is None:
        return ()
    wrapper = effects[1]
    try:
        children = validate_second_ability_draw(wrapper)
    except (TypeError, ValueError):
        return ()
    return _ordinal_component_capabilities(effects, target_schema, mechanic_ids, children)


def _ordinal_component_capabilities(effects, target_schema, mechanic_ids, children):
    from .capabilities import capability_dependencies_for_node

    mechanics = set(mechanic_ids)
    wrapper = effects[1]
    prefix_mechanics = set(wrapper["prefix_mechanic_ids"])
    result_mechanics = set(wrapper["mechanic_ids"])
    condition_mechanic = "fixed-resolution-public-condition"
    if not {condition_mechanic, *prefix_mechanics, *result_mechanics} <= mechanics:
        return ()
    prefix = capability_dependencies_for_node(
        effects=effects[:1], target_schema=target_schema,
        mechanic_ids=mechanics - {condition_mechanic, *result_mechanics} | prefix_mechanics,
    )
    result = capability_dependencies_for_node(effects=children, target_schema=None, mechanic_ids=result_mechanics)
    if not prefix or not result:
        return ()
    return tuple(sorted({*_ORDINAL_CAPABILITIES, *prefix, *result}))


def second_ability_draw_runtime_dependencies(program):
    if not any(is_second_ability_draw(effect) for effect in program.effects):
        return ()
    if (program.active_zone != "battlefield" or not program.event
            or program.event != _prefix_event(program.effects, program.target_schema)):
        raise ValueError("The second-resolution draw has the wrong normalized occurrence")
    required = second_ability_draw_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("The second-resolution draw lacks its complete prefix and result authority")
    return required


_TRAMPLE = {"op": "grant_keyword_until_end_of_turn", "card": "$target.0", "keyword": "Trample"}
_CREATURE_SET_BUFF = {
    "op": "modify_all_matching_permanents_until_end_of_turn",
    "predicate": ordinal_controller_creatures("$controller"),
    "power": 1, "toughness": 0,
}
_THIRD_ACTIVATION_COSTS = {
    "offer_optional_effect": ("{2}", 2, 0),
    "mana": ("{R}", 0, 1),
    "modify_all_matching_permanents_until_end_of_turn": ("{2}{R}", 2, 1),
}
_SOURCE_COST_FLAGS = ("tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source")


def _third_prefix_matches(prefix, target_schema, result_operation):
    effect = thaw_value(FrozenMap(prefix))
    if result_operation == "modify_all_matching_permanents_until_end_of_turn":
        return (
            target_schema is None and effect == _CREATURE_SET_BUFF
            and type(effect["power"]) is int and type(effect["toughness"]) is int
        )
    if not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int:
        return False
    expected_target = dict(_CREATURE_TARGET)
    if result_operation == "offer_optional_effect":
        del expected_target["controller_relation"]
    return effect == _TRAMPLE and thaw_value(FrozenMap(target_schema)) == expected_target


def third_ability_result_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 2:
        return ()
    try:
        children = validate_third_ability_result(effects[1])
    except (TypeError, ValueError):
        return ()
    if not _third_prefix_matches(effects[0], target_schema, children[0]["op"]):
        return ()
    return _ordinal_component_capabilities(effects, target_schema, mechanic_ids, children)


def third_ability_result_cost_is_closed(effect, cost_schema):
    if not isinstance(cost_schema, Mapping):
        return False
    try:
        child, = validate_third_ability_result(effect)
    except (TypeError, ValueError):
        return False
    text, generic, red = _THIRD_ACTIVATION_COSTS[child["op"]]
    expected = {
        "text": text, "mana": {"GENERIC": generic, "R": red, **dict.fromkeys("WUBGC", 0)},
        "complex_symbols": [], "choices": [], "uncompiled_costs": [],
        "life_payment": 0, "energy_payment": 0,
        "loyalty_delta": None, "source_counter_removal_cost": None,
        **dict.fromkeys(_SOURCE_COST_FLAGS, False),
    }
    cost = thaw_value(FrozenMap(cost_schema))
    return (
        cost == expected and all(type(amount) is int for amount in cost["mana"].values())
        and all(cost[field] is False for field in _SOURCE_COST_FLAGS)
        and type(cost["life_payment"]) is int and type(cost["energy_payment"]) is int
    )


def third_ability_result_runtime_dependencies(program):
    effects = program.effects
    if not any(is_third_ability_result(effect) for effect in effects):
        return ()
    if (program.active_zone != "battlefield" or program.event != "activate"
            or program.event_condition is not None or len(effects) != 2
            or not third_ability_result_cost_is_closed(effects[1], program.cost_schema)):
        raise ValueError("A third-resolution result requires its exact nonmana battlefield activation and price")
    required = third_ability_result_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("The third-resolution ability lacks its complete prefix and result authority")
    return required
