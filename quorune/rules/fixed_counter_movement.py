"""Closed target roles and payments for replacement-aware counter movement."""

from collections.abc import Mapping

from ..fixed_counter_movement_model import (
    CHOSEN_COUNTER_MOVEMENT_CAPABILITY, CHOSEN_COUNTER_QUANTITIES_CAPABILITY,
    CHOSEN_OPPONENT_COUNTER_MOVEMENT_CAPABILITY,
    CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED, CONTROLLED_TARGET_TO_SOURCE,
    FIXED_COUNTER_MOVEMENT_CAPABILITY, FIXED_COUNTER_MOVEMENT_OPERATION,
    OPTIONAL_CHOSEN_CONTROLLED_CREATURES, OPTIONAL_COMBAT_COUNTER_MOVEMENT_CAPABILITY,
    FixedCounterMovementSpec, TWO_DISTINCT_TARGETS,
    OPTIONAL_COMBAT_ALL_COUNTER_MOVEMENT_CAPABILITY, SOURCE_HAS_COUNTERS,
)
from ..replacement.immutable import FrozenMap
from .cumulative_object_upkeep import CONTROLLER_UPKEEP
from .effect_shape_walkers import nested_effects
from .source_counter_distribution import (
    SOURCE_COUNTER_DISTRIBUTION_CAPABILITY, source_counter_distribution_node_capabilities,
    source_counter_distribution_program_dependencies,
    uses_source_counter_distribution,
)


FIXED_COUNTER_MOVEMENT_MECHANICS = ("cr-122-counters", "cr-115-targets")
CONTROLLER_BEGINNING_COMBAT = FrozenMap({"all": [
    {"field": "player", "op": "eq", "value": "$source.controller"},
    {"field": "step", "op": "eq", "value": "beginning_combat"},
]})


SOURCE_COUNTERS_BEGINNING_COMBAT = FrozenMap({"all": [
    *CONTROLLER_BEGINNING_COMBAT["all"],
    {"field": SOURCE_HAS_COUNTERS, "op": "eq", "value": True},
]})


_CHOSEN_MOVE_COST = FrozenMap({
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


_CHOSEN_QUANTITIES_COST = FrozenMap({
    **_CHOSEN_MOVE_COST, "text": "{4}{W}", "mana": {**_CHOSEN_MOVE_COST["mana"], "GENERIC": 4, "W": 1},
    "tap_source": False,
})


def chosen_counter_movement_price_is_closed(ability, *, quantities=False):
    from ..compiler.activated_costs import activated_ability_cost

    return (
        ability.zones == ("battlefield",) and not ability.mana_ability and ability.sorcery_speed == (not quantities)
        and not ability.activation_conditions and not ability.generic_reduction_per_legendary_creature
        and FrozenMap(activated_ability_cost(ability)) == (_CHOSEN_QUANTITIES_COST if quantities else _CHOSEN_MOVE_COST)
    )


def fixed_counter_movement_target_schema(spec):
    creature = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}
    if spec.form == CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED:
        return {"groups": [
            {"id": "donor", **creature, "controller_relation": "opponent"},
            {"id": "recipient", **creature, "controller_relation": "you"},
        ], "globally_distinct": True}
    if spec.form == OPTIONAL_CHOSEN_CONTROLLED_CREATURES:
        creature["controller_relation"] = "you"
        return {"groups": [{"id": "donor", **creature}, {"id": "recipient", **creature}], "globally_distinct": True}
    if spec.chooses_counter_kind or spec.chooses_counter_quantities:
        permanent = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}
        return {
            "groups": [
                {"id": "donor", **permanent, "controller_relation": "you"},
                {"id": "recipient", **permanent, **({"controller_relation": "you"} if spec.chooses_counter_quantities else {})},
            ],
            "globally_distinct": True,
        }
    if spec.form == TWO_DISTINCT_TARGETS:
        return {"groups": [{"id": "donor", **creature}, {"id": "recipient", **creature}], "globally_distinct": True}
    if spec.form == CONTROLLED_TARGET_TO_SOURCE:
        creature["controller_relation"] = "you"
    return creature


def fixed_counter_movement_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if any(uses_source_counter_distribution(effect) for effect in effects):
        return source_counter_distribution_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids, cost_schema=cost_schema,
        )
    if (len(effects) != 1 or not isinstance(target_schema, Mapping)
            or not set(FIXED_COUNTER_MOVEMENT_MECHANICS).issubset(mechanic_ids)):
        return ()
    try:
        spec = FixedCounterMovementSpec.from_effect(effects[0])
        if spec.optional and cost_schema is not None:
            return ()
        if FrozenMap(target_schema) != FrozenMap(fixed_counter_movement_target_schema(spec)):
            return ()
    except (KeyError, TypeError, ValueError):
        return ()
    required = {
        FIXED_COUNTER_MOVEMENT_CAPABILITY, "counter.producer.fixed_effect",
        "counter.placement.quantity_replacement", "target.revalidate_resolution",
    }
    if spec.optional:
        required.update(("trigger.effect.fixed_event", "trigger.placement.apnap"))
    if spec.moves_all_source_counters:
        required.update((OPTIONAL_COMBAT_ALL_COUNTER_MOVEMENT_CAPABILITY, "state_query.permanent.any_counter"))
    elif spec.form == OPTIONAL_CHOSEN_CONTROLLED_CREATURES:
        required.add(OPTIONAL_COMBAT_COUNTER_MOVEMENT_CAPABILITY)
    elif spec.form == CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED:
        required.add(CHOSEN_OPPONENT_COUNTER_MOVEMENT_CAPABILITY)
    elif spec.chooses_counter_kind:
        required.add(CHOSEN_COUNTER_MOVEMENT_CAPABILITY)
    elif spec.chooses_counter_quantities:
        required.add(CHOSEN_COUNTER_QUANTITIES_CAPABILITY)
    return tuple(sorted(required))


def _validate_activation_catalog(program):
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    handlers = program.handlers
    if ((program.event, program.active_zone) != ("activate", "battlefield")
            or len(handlers) != 1 or handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Mandatory counter movement requires the original battlefield activation")
    abilities = activated_abilities_from_descriptors(handlers)
    if len(abilities) != 1 or program.ability_id != f"ability:{abilities[0].ability_id}":
        raise ValueError("Counter movement lost its original activation catalog")
    ability = abilities[0]
    if ability.mana_ability or ability.uncompiled_costs:
        raise ValueError("Counter movement requires a complete ordinary activation price")
    return ability


def fixed_counter_movement_program_dependencies(program):
    from .departure_counter_copy import departure_counter_copy_program_dependencies

    if departure := departure_counter_copy_program_dependencies(program):
        return departure
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                   if effect.get("op") == FIXED_COUNTER_MOVEMENT_OPERATION or uses_source_counter_distribution(effect))
    if (SOURCE_COUNTER_DISTRIBUTION_CAPABILITY in program.capability_dependencies
            or any(uses_source_counter_distribution(effect) for effect in marked)):
        return source_counter_distribution_program_dependencies(program, marked=marked)
    if not marked and not {
        FIXED_COUNTER_MOVEMENT_CAPABILITY, CHOSEN_COUNTER_MOVEMENT_CAPABILITY,
        OPTIONAL_COMBAT_COUNTER_MOVEMENT_CAPABILITY, CHOSEN_COUNTER_QUANTITIES_CAPABILITY,
        CHOSEN_OPPONENT_COUNTER_MOVEMENT_CAPABILITY,
        OPTIONAL_COMBAT_ALL_COUNTER_MOVEMENT_CAPABILITY,
    }.intersection(program.capability_dependencies):
        return ()
    if len(marked) != 1 or effects != marked:
        raise ValueError("Counter movement requires its complete original effect")
    spec = FixedCounterMovementSpec.from_effect(effects[0])
    if spec.optional:
        expected = (SOURCE_COUNTERS_BEGINNING_COMBAT if spec.moves_all_source_counters
                    else CONTROLLER_BEGINNING_COMBAT if spec.form == OPTIONAL_CHOSEN_CONTROLLED_CREATURES
                    else CONTROLLER_UPKEEP)
        if ((program.event, program.active_zone) != ("step.begin", "battlefield")
                or program.handlers or program.cost_schema is not None
                or FrozenMap(program.event_condition or {}) != FrozenMap(expected)):
            raise ValueError("Optional movement requires its exact original controller step")
    else:
        ability = _validate_activation_catalog(program)
        expected_cost = _CHOSEN_QUANTITIES_COST if spec.chooses_counter_quantities else _CHOSEN_MOVE_COST
        if (spec.chooses_counter_kind or spec.chooses_counter_quantities) and (
            program.event_condition is not None or ability.builtin_semantic_key != program.key
            or ability.target_schema != FrozenMap(program.target_schema or {})
            or not isinstance(program.cost_schema, Mapping)
            or FrozenMap(program.cost_schema) != expected_cost
            or not chosen_counter_movement_price_is_closed(ability, quantities=spec.chooses_counter_quantities)
        ):
            raise ValueError("Chosen counter movement requires its exact public target roles and printed activation price")
    required = fixed_counter_movement_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        cost_schema=program.cost_schema,
    )
    if not required:
        raise ValueError("Counter movement lost its original target roles")
    return required


def is_closed_fixed_counter_movement_program(program):
    from .departure_counter_copy import uses_departure_counter_copy

    if not any(effect.get("op") == FIXED_COUNTER_MOVEMENT_OPERATION
               or uses_source_counter_distribution(effect) or uses_departure_counter_copy(effect)
               for effect in program.effects):
        return False
    try:
        required = fixed_counter_movement_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
