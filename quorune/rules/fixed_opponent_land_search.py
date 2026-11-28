from __future__ import annotations

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


FIELD = "fixed_opponent_land_search"
MECHANIC = "fixed-library-search-to-battlefield"
CAPABILITY = "library.search.fixed_opponent_land_offer"
DEPENDENCIES = (
    CAPABILITY, "library.search.fixed_to_battlefield", "library.shuffle.fixed_controller",
    "library.search.opponent_control_and_exile", "trigger.placement.apnap", "zone.change.destination_replacement",
)


def opponent_land_search_effect():
    return {"op": "choose_cards_apnap", FIELD: 1}


def uses_opponent_land_search(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "choose_cards_apnap" and FIELD in effect


def opponent_land_search_node_capabilities(effects, target_schema, cost_schema, mechanics):
    if not any(uses_opponent_land_search(effect) for effect in effects):
        return None
    if (target_schema is not None or cost_schema is not None or len(effects) != 1
            or thaw_value(effects[0]) != opponent_land_search_effect()
            or type(effects[0].get(FIELD)) is not int):
        return ()
    return DEPENDENCIES


def opponent_land_search_program_dependencies(program):
    if not any(uses_opponent_land_search(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = opponent_land_search_node_capabilities(
        program.effects, program.target_schema, program.cost_schema, program.coverage,
    )
    if (not required or MECHANIC not in program.coverage or program.handlers or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")):
        raise ValueError("An opponent land search requires one complete untargeted spell")
    return required
