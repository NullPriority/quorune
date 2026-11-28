"""Exact loyalty instructions for public silver-counter exile results."""
from collections.abc import Mapping

from ..public_library_piles_model import PILE_CAPABILITY, PILE_OPERATION, PILE_SILVER_CAPABILITY, PILE_SILVER_VERSION
from ..replacement.immutable import thaw_value


SILVER_EXILE_RETURN_FIELD = "owned_silver_exile_return"
SILVER_EXILE_RETURN_CAPABILITY = "zone.exile.owned_silver_counter_card_to_hand"
SILVER_EXILE_RETURN_MECHANIC = "owned-silver-exile-return"
SILVER_EXILE_RETURN_CANDIDATES = "_silver_exile_return_incarnations"


def silver_pair_effect():
    return {"op": PILE_OPERATION, "player": "$controller", "schema_version": PILE_SILVER_VERSION,
            "look_count": 2, "opponent_role": "chooser"}


def silver_return_effect(player="$controller"):
    return {"op": "choose_objects", "player": player, SILVER_EXILE_RETURN_FIELD: {"schema_version": 1}}


def is_silver_pair(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == PILE_OPERATION
            and effect.get("schema_version") == PILE_SILVER_VERSION)


def is_silver_return(effect, *, player="$controller"):
    if not isinstance(effect, Mapping):
        return False
    marker = effect.get(SILVER_EXILE_RETURN_FIELD)
    return (isinstance(marker, Mapping) and type(marker.get("schema_version")) is int
            and thaw_value(effect) == silver_return_effect(player))


def _closed_loyalty_price(cost, delta):
    if not isinstance(cost, Mapping):
        return False
    expected = {
        "mana": {symbol: 0 for symbol in ("GENERIC", *"WUBRGC")},
        "complex_symbols": [], "tap_source": False, "untap_source": False, "discard_source": False,
        "sacrifice_source": False, "exile_source": False, "life_payment": 0, "energy_payment": 0,
        "loyalty_delta": delta, "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
    }
    return (set(cost) == set(expected) | {"text"}
            and type(cost["text"]) is str and isinstance(cost["mana"], Mapping)
            and all(type(value) is int for value in cost["mana"].values())
            and all(type(cost[key]) is type(value) for key, value in expected.items() if type(value) in (bool, int))
            and thaw_value({key: cost[key] for key in expected}) == expected)


def silver_loyalty_dependencies(*, effects, target_schema, active_zone, cost_schema):
    if len(effects) != 1 or target_schema is not None or active_zone != "battlefield":
        return ()
    effect = effects[0]
    if is_silver_pair(effect) and type(effect["schema_version"]) is int and thaw_value(effect) == silver_pair_effect():
        return (PILE_CAPABILITY, PILE_SILVER_CAPABILITY) if _closed_loyalty_price(cost_schema, 1) else ()
    if is_silver_return(effect):
        return (SILVER_EXILE_RETURN_CAPABILITY,) if _closed_loyalty_price(cost_schema, -1) else ()
    return ()


def silver_loyalty_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects
    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID, ActivatedAbilityCatalogHandler

    if not any(is_silver_pair(effect) or SILVER_EXILE_RETURN_FIELD in effect
               for effect in nested_effects((*program.effects, *program.handlers))):
        return ()
    required = silver_loyalty_dependencies(effects=program.effects, target_schema=program.target_schema,
        active_zone=program.active_zone, cost_schema=program.cost_schema)
    if (not required or program.event != "activate" or program.event_condition is not None
            or len(program.handlers) != 1 or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Silver exile requires its exact loyalty activation")
    descriptor = program.handlers[0]
    ability = ActivatedAbilityCatalogHandler().validate(descriptor)
    cost = program.cost_schema
    if (ability.builtin_semantic_key != program.key or ability.cost_text != cost["text"]
            or ability.mana_ability or ability.zones != ("battlefield",) or ability.target_schema is not None
            or ability.activation_conditions or ability.activation_limit is not None or ability.mana_cost_options
            or any(thaw_value(descriptor["ability"].get(key)) != thaw_value(value)
                   for key, value in cost.items() if key != "text")):
        raise ValueError("Silver exile loyalty price differs from its activation catalog")
    return required


def is_closed_silver_loyalty_program(program):
    try:
        required = silver_loyalty_runtime_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
