"""Bind two ordered draw-and-land pairs to the activator and original owner."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value


CAPABILITY = "resolution.effect_program.controller_owner_draw_land"
MECHANIC = "ordered-controller-owner-draw-land"
TEMPLATE = "ordered-controller-owner-draw-land-v1"
FIELD = "original_owner_draw_land_version"
OWNER_CONTEXT = "draw_land_original_source_owner"
OWNER_HAND_MOVE = "original_source_owner_hand_entry_version"
REQUIREMENTS = (CAPABILITY, "zone.draw.library_to_hand", "zone.move.fixed_private_hand_choice",
                "zone.change.destination_replacement")


def _land_entry():
    return {"op": "put_card_from_hand", "player": "$controller", "tapped": False,
            "query": ObjectQuerySpec(zones=("hand",), types_all=("land",)).canonical_dict()}


def controller_owner_draw_land_effects():
    draw = {"op": "draw", "player": "$controller", "count": 1, "private": True}
    land = _land_entry()
    return (draw, land, {**draw, FIELD: 1}, {**land, FIELD: 1})


def has_pendant_price(cost):
    if not isinstance(cost, Mapping):
        return False
    untouched = ("untap_source", "discard_source", "sacrifice_source", "exile_source")
    normalized = {
        "text": "{2}, {T}", "mana": {**dict.fromkeys("WUBRGC", 0), "GENERIC": 2},
        "complex_symbols": [], "tap_source": True, **dict.fromkeys(untouched, False),
        "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
        "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
    }
    return (FrozenMap(cost) == FrozenMap(normalized) and cost["tap_source"] is True
            and all(cost[key] is False for key in untouched)
            and all(type(cost[key]) is int for key in ("life_payment", "energy_payment"))
            and all(type(amount) is int for amount in cost["mana"].values()))


def ordered_source_owner_hand_entry_capabilities(effects, target_schema, active_zone, cost_schema, mechanics):
    if (target_schema is not None or active_zone != "battlefield" or MECHANIC not in mechanics
            or not has_pendant_price(cost_schema)
            or tuple(FrozenMap(effect) for effect in effects) != tuple(map(FrozenMap, controller_owner_draw_land_effects()))):
        return ()
    if any(type(effect["count"]) is not int or effect["private"] is not True
           for effect in effects if effect["op"] == "draw"):
        return ()
    if any(effect["tapped"] is not False for effect in effects if effect["op"] == "put_card_from_hand"):
        return ()
    return REQUIREMENTS if all(type(effect[FIELD]) is int for effect in effects[2:]) else ()


def ordered_source_owner_hand_entry_dependencies(program):
    if CAPABILITY not in program.capability_dependencies and not any(FIELD in effect for effect in program.effects):
        return ()
    required = ordered_source_owner_hand_entry_capabilities(
        program.effects, program.target_schema, program.active_zone, program.cost_schema, program.coverage,
    )
    if (not required or program.event != "activate" or program.event_condition is not None
            or program.trigger_limit is not None or len(program.handlers) != 1):
        raise ValueError("Ordered owner land entry requires its original complete activation")
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (ability.builtin_semantic_key != program.key or ability.zones != ("battlefield",) or ability.mana_ability
            or ability.target_schema is not None or activated_ability_cost(ability) != thaw_value(program.cost_schema)):
        raise ValueError("Ordered owner land entry requires its pinned activation price")
    return required


def is_closed_ordered_source_owner_hand_entry(program):
    try:
        required = ordered_source_owner_hand_entry_dependencies(program)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def capture_source_owner_draw_land(program, source):
    if program is None or CAPABILITY not in program.capability_dependencies:
        return {}
    return {OWNER_CONTEXT: source.owner}


def bind_original_owner_draw_land(host, item, effect):
    program = host.semantics.get(item.semantic_key)
    owner = item.context.get(OWNER_CONTEXT)
    if (item.kind != "activated_ability" or item.context.get("currently_resolving") is not True
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not ordered_source_owner_hand_entry_dependencies(program)
            or type(effect.get(FIELD)) is not int
            or FrozenMap(effect) not in tuple(map(FrozenMap, controller_owner_draw_land_effects()[2:]))
            or type(owner) is not str or owner not in host.seats):
        raise ValueError("Ordered owner land entry lost its original activated source owner")
    if owner not in host.active_seats:
        return None
    bound = {**{key: value for key, value in effect.items() if key != FIELD}, "player": owner}
    if effect["op"] == "put_card_from_hand":
        bound[OWNER_HAND_MOVE] = 1
    return bound


def original_owner_hand_move_shape(value, query, tapped):
    return (type(value) is int and value == 1 and tapped is False and isinstance(query, Mapping)
            and FrozenMap(query) == FrozenMap(_land_entry()["query"]))


def resolve_original_owner_hand_move(host, effect, *, stack_controller):
    """Verify the private chooser's role again before moving their selected land."""
    owner = effect.get("controller")
    active = [item for item in host.state.stack if item.context.get("currently_resolving") is True]
    if (len(active) != 1 or active[0].controller != stack_controller
            or not original_owner_hand_move_shape(effect.get(OWNER_HAND_MOVE), effect.get("hand_entry_query"),
                                                  effect.get("tapped"))):
        raise ValueError("Selected source-owner land entry lost its original activation")
    expected = bind_original_owner_draw_land(host, active[0], controller_owner_draw_land_effects()[3])
    if expected is None or owner != expected["player"]:
        raise ValueError("Selected land entry must use the original artifact owner")
    return owner, {key: value for key, value in effect.items() if key != OWNER_HAND_MOVE}
