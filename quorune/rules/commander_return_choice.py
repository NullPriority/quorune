from __future__ import annotations

"""The closed nontargeted command-zone return on a paid source activation."""

from typing import Mapping

from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


COMMANDER_RETURN_FIELD = "own_commander_return"
COMMANDER_RETURN_MECHANIC = "own-commander-command-return"
COMMANDER_RETURN_CAPABILITY = "zone.return.own_commander_choice"

_COST_FLAGS = {
    "tap_source": True,
    "sacrifice_source": True,
    "untap_source": False,
    "discard_source": False,
    "exile_source": False,
}
_EMPTY_PRICE = {"complex_symbols", "choices", "uncompiled_costs"}
_INTEGER_PRICE = {"life_payment", "energy_payment"}
_NULL_PRICE = {"loyalty_delta", "source_counter_removal_cost"}
_PRICE_FIELDS = set(_COST_FLAGS) | _EMPTY_PRICE | _INTEGER_PRICE | _NULL_PRICE | {"text", "mana"}


def commander_return_effect(actor="$controller"):
    return {"op": "choose_objects", "player": actor, COMMANDER_RETURN_FIELD: {"schema_version": 1}}


def is_commander_return_effect(effect, *, actor="$controller"):
    marker = effect.get(COMMANDER_RETURN_FIELD)
    return (isinstance(marker, Mapping) and type(marker.get("schema_version")) is int
            and thaw_value(effect) == commander_return_effect(actor))


def commander_return_price_is_closed(price):
    if not isinstance(price, Mapping) or set(price) != _PRICE_FIELDS:
        return False
    mana = price["mana"]
    return (isinstance(mana, Mapping) and set(mana) == set("WUBRGC") | {"GENERIC"}
            and all(type(amount) is int and amount == 0 for amount in mana.values())
            and all(price[name] is flag for name, flag in _COST_FLAGS.items())
            and all(isinstance(price[name], (list, tuple)) and not price[name] for name in _EMPTY_PRICE)
            and all(type(price[name]) is int and price[name] == 0 for name in _INTEGER_PRICE)
            and all(price[name] is None for name in _NULL_PRICE)
            and type(price["text"]) is str and bool(price["text"]))


def commander_return_node_capabilities(*, effects, target_schema, mechanic_ids, **_unused):
    if (COMMANDER_RETURN_MECHANIC in mechanic_ids and target_schema is None and len(effects) == 1
            and is_commander_return_effect(effects[0])):
        return (COMMANDER_RETURN_CAPABILITY,)
    return ()


def commander_return_runtime_dependencies(program):
    if not any(COMMANDER_RETURN_FIELD in effect for effect in nested_effects(program.effects)):
        return ()
    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

    price = program.cost_schema
    if (
        (program.event, program.active_zone) != ("activate", "battlefield")
        or program.target_schema is not None or program.event_condition is not None
        or len(program.effects) != 1 or not is_commander_return_effect(program.effects[0])
        or not commander_return_price_is_closed(price)
        or len(program.handlers) != 1
        or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID
    ):
        raise ValueError("Commander return requires its exact paid battlefield activation")
    ability = program.handlers[0].get("ability")
    if (
        not isinstance(ability, Mapping) or ability.get("mana_ability") is not False
        or ability.get("target_schema") is not None or ability.get("cost_text") != price["text"]
        or tuple(ability.get("zones") or ()) != ("battlefield",)
        or not all(thaw_value(ability.get(name)) == thaw_value(price[name]) for name in _PRICE_FIELDS - {"text"})
    ):
        raise ValueError("Commander return price differs from the activated ability catalog")
    return (COMMANDER_RETURN_CAPABILITY,)


def is_closed_commander_return_program(program):
    try:
        required = commander_return_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
