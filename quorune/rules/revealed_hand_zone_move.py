from __future__ import annotations

"""Closed revealed hand-source land movement on a paid ordinary activation."""

from collections.abc import Mapping
import re

from ..replacement.immutable import thaw_value


FIELD = "revealed_hand_land_entry"
MECHANIC = "revealed-hand-land-entry"
CAPABILITY = "zone.self_move.revealed_hand_land_entry"
SPEC_VERSION = 4
BODY = "Put this card from your hand onto the battlefield."


def revealed_hand_entry_cost_is_closed(ability):
    match = re.fullmatch(r"\{([1-9]\d*)\}", ability.cost_text)
    if match is None or ability.zones != ("hand",) or " ".join(ability.effect_text.split()) != BODY:
        return False
    mana = dict.fromkeys(("GENERIC", *"WUBRGC"), 0)
    mana["GENERIC"] = int(match[1])
    return dict(ability.mana) == mana and not any(getattr(ability, field) for field in (
        "tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source",
        "life_payment", "energy_payment", "choices", "uncompiled_costs", "mana_ability",
        "mana_cost_options", "sorcery_speed", "waterbend", "complex_symbols", "activation_conditions",
        "random_discard_payment", "mill_library_cost", "loyalty_x", "forecast", "boast", "reconfigure",
        "generic_reduction_per_legendary_creature", "library_search_types", "fixed_mana_outputs",
    )) and all(getattr(ability, field) is None for field in (
        "target_schema", "loyalty_delta", "source_counter_removal_cost", "source_counter_placement_cost",
        "activation_limit", "announced_x_symbols", "variable_colored_reveal_price", "mana_spend_restriction",
        "dynamic_mana_output", "builtin_semantic_key", "crew_threshold", "saddle_threshold",
        "fixed_mana_result", "color_set_mana_output", "power_up", "granting_attachment_payment",
        "ability_local_mana_reduction",
    ))


def revealed_hand_entry_effect():
    return {"op": "self_zone_move", "origin": "hand", "destination": "battlefield",
            "tapped": False, "source_form": "land", FIELD: 1}


def revealed_hand_entry_payload_dependencies(effect, *, runtime=False):
    if FIELD not in effect and effect.get("origin") != "hand":
        return ()
    reduced = {key: value for key, value in effect.items()
               if not runtime or key != "_replacement_selections"}
    if (type(reduced.get(FIELD)) is not int or reduced.get("tapped") is not False
            or reduced != revealed_hand_entry_effect()):
        raise ValueError("Hand-source land entry requires the exact revealed-source instruction")
    return (CAPABILITY, "zone.self_move.activated", "zone.change.destination_replacement")


def revealed_hand_entry_node_capabilities(*, effects, target_schema, mechanic_ids):
    if MECHANIC not in mechanic_ids or len(effects) != 1 or target_schema is not None:
        return ()
    try:
        return revealed_hand_entry_payload_dependencies(effects[0])
    except ValueError:
        return ()


def revealed_hand_entry_runtime_dependencies(program):
    from ..self_zone_move import SELF_ZONE_MOVE_ABILITY_HANDLER_ID, SelfZoneMoveSpec

    descriptors = tuple(handler for handler in program.handlers
                        if handler.get("handler_id") == SELF_ZONE_MOVE_ABILITY_HANDLER_ID)
    from .effect_shape_walkers import nested_effects

    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if FIELD in effect or effect.get("op") == "self_zone_move" and effect.get("origin") == "hand")
    hand_descriptors = tuple(handler for handler in descriptors
                             if isinstance(handler.get("move"), Mapping) and handler["move"].get("origin") == "hand")
    if not marked and not hand_descriptors:
        return ()
    if ((program.event, program.active_zone) != ("activate", "hand") or len(marked) != 1
            or len(program.effects) != 1 or tuple(program.effects) != marked or len(hand_descriptors) != 1
            or program.target_schema is not None or program.event_condition is not None):
        raise ValueError("Revealed hand entry needs one ordinary original hand activation")
    spec = SelfZoneMoveSpec.from_dict(hand_descriptors[0]["move"])
    others = tuple(handler for handler in program.handlers if handler is not hand_descriptors[0])
    if others:
        from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

        expected_catalog = {**spec.ability.to_dict(), "builtin_semantic_key": program.key}
        if (len(others) != 1 or others[0].get("handler_id") != "activation.catalog.pinned.v1"
                or tuple(ability.to_dict() for ability in activated_abilities_from_descriptors(others)) != (expected_catalog,)):
            raise ValueError("Revealed hand land entry changed its original hand activation catalog")
    cost = program.cost_schema
    expected = {
        "text": spec.ability.cost_text, "mana": dict(spec.ability.mana), "complex_symbols": [],
        "tap_source": False, "untap_source": False, "discard_source": False, "sacrifice_source": False,
        "exile_source": False, "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
        "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
    }
    if (spec.schema_version != SPEC_VERSION or not isinstance(cost, Mapping) or thaw_value(cost) != expected
            or any(type(cost[field]) is not type(value) for field, value in expected.items()
                   if type(value) in (bool, int, str))):
        raise ValueError("Revealed hand entry lost its verified activation price")
    return revealed_hand_entry_payload_dependencies(marked[0])
