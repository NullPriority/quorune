"""Seal an announced opponent separately from the creature they choose at resolution."""

from ..counter_placement_targets import (
    CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD, OPPONENT_CREATURE_COUNTER_CHOICE_VERSION,
    controlled_creature_counter_choice_dependencies,
)
from ..replacement.immutable import freeze_value, thaw_value
from .effect_shape_walkers import nested_effects


OPPONENT_COUNTER_CHOICE_TARGET = {
    "zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1,
}


def is_opponent_counter_choice(value):
    return value.get(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD) == OPPONENT_CREATURE_COUNTER_CHOICE_VERSION


def target_opponent_counter_choice_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or not is_opponent_counter_choice(effects[0]) or "cr-122-counters" not in mechanic_ids:
        return ()
    schema = thaw_value(freeze_value(target_schema))
    if schema != OPPONENT_COUNTER_CHOICE_TARGET or type(schema["count"]) is not int:
        return ()
    try:
        owners = controlled_creature_counter_choice_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()
    return (*owners, "trigger.event.normalized_zone_change", "trigger.placement.apnap")


def target_opponent_counter_choice_runtime_dependencies(program):
    marked = [value for value in nested_effects((*program.effects, *getattr(program, "handlers", ())))
              if is_opponent_counter_choice(value)]
    if not marked:
        return ()
    required = target_opponent_counter_choice_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (len(marked) != 1 or not required or program.event != "permanent.enter.self"
            or program.active_zone != "battlefield" or program.event_condition is not None
            or program.cost_schema is not None or getattr(program, "handlers", ())
            or not program.ability_id.startswith("trigger:")):
        raise ValueError("An opponent counter choice requires its complete source entry trigger")
    return required
