"""Closed resolution quantity for repeated ordinary Proliferate."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


CONTROLLED_ARRIVAL_PROLIFERATE_OPERATION = "proliferate_for_controlled_nontoken_arrivals"
CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY = "counter.producer.proliferate_for_controlled_nontoken_arrivals"
CONTROLLED_ARRIVAL_PROLIFERATE_MECHANIC = "proliferate-for-controlled-nontoken-arrivals"
CONTROLLED_ARRIVAL_PROLIFERATE_DEPENDENCIES = (
    CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY, "counter.producer.proliferate",
    "counter.placement.quantity_replacement",
)
_COST = FrozenMap({
    "text": "{1}, {T}",
    "mana": {key: int(key == "GENERIC") for key in ("B", "C", "G", "GENERIC", "R", "U", "W")},
    "complex_symbols": [],
    "tap_source": True,
    "untap_source": False,
    "discard_source": False,
    "sacrifice_source": False,
    "exile_source": False,
    "life_payment": 0,
    "energy_payment": 0,
    "loyalty_delta": None,
    "source_counter_removal_cost": None,
    "choices": [],
    "uncompiled_costs": [],
})


def controlled_arrival_proliferate_effect():
    return {"op": CONTROLLED_ARRIVAL_PROLIFERATE_OPERATION, "player": "$controller"}


def controlled_arrival_proliferate_price_is_closed(ability):
    from ..compiler.activated_costs import activated_ability_cost

    return (
        ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
        and not ability.activation_conditions and not ability.generic_reduction_per_legendary_creature
        and FrozenMap(activated_ability_cost(ability)) == _COST
    )


def controlled_arrival_proliferate_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if (target_schema is None and isinstance(cost_schema, Mapping) and FrozenMap(cost_schema) == _COST
            and len(effects) == 1 and FrozenMap(effects[0]) == FrozenMap(controlled_arrival_proliferate_effect())
            and CONTROLLED_ARRIVAL_PROLIFERATE_MECHANIC in mechanic_ids):
        return CONTROLLED_ARRIVAL_PROLIFERATE_DEPENDENCIES
    return ()


def controlled_arrival_proliferate_program_dependencies(program):
    effects = tuple(program.effects)
    matches = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                    if effect.get("op") == CONTROLLED_ARRIVAL_PROLIFERATE_OPERATION)
    if (not matches and CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY not in program.capability_dependencies
            and CONTROLLED_ARRIVAL_PROLIFERATE_MECHANIC not in program.coverage):
        return ()
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    required = controlled_arrival_proliferate_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        cost_schema=program.cost_schema,
    )
    if (not required or effects != matches or program.event_condition is not None
            or (program.event, program.active_zone) != ("activate", "battlefield")
            or len(program.handlers) != 1
            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Repeated Proliferate requires its original untargeted battlefield activation")
    abilities = activated_abilities_from_descriptors(program.handlers)
    if (len(abilities) != 1 or program.ability_id != f"ability:{abilities[0].ability_id}"
            or abilities[0].builtin_semantic_key != program.key or abilities[0].target_schema
            or not controlled_arrival_proliferate_price_is_closed(abilities[0])):
        raise ValueError("Repeated Proliferate lost its printed mana and tap payment")
    return required


def is_closed_controlled_arrival_proliferate_program(program):
    try:
        required = controlled_arrival_proliferate_program_dependencies(program)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
