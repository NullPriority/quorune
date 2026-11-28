"""Closed combat rule for a creature that received counters from the trigger controller."""

from ..counter_placement_event_model import COUNTER_EVENT_CAPABILITY, COUNTER_PLACEMENT_EVENT
from ..declaration_rule_effects import OPPONENT_BLOCKING_PROHIBITION_KIND
from ..event_counter_reference import EVENT_COUNTER_OBJECT
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


COUNTER_PLACEMENT_BLOCKING_CAPABILITY = "combat.declaration.counter_recipient_opponent_blockers"
COUNTER_PLACEMENT_BLOCKING_MECHANIC = "counter-recipient-opponent-blockers"
COUNTER_PLACEMENT_BLOCKING_TEMPLATE = "counter-recipient-opponent-blockers-v1"
COUNTER_PLACEMENT_BLOCKING_DEPENDENCIES = (
    COUNTER_PLACEMENT_BLOCKING_CAPABILITY, COUNTER_EVENT_CAPABILITY,
    "combat.declaration.typed_components", "trigger.effect.fixed_event", "trigger.placement.apnap",
)
COUNTER_PLACEMENT_BLOCKING_MECHANICS = (
    COUNTER_PLACEMENT_BLOCKING_MECHANIC, "cr-509-declare-blockers-step", "cr-611-continuous-effects",
)


def counter_placement_blocking_condition():
    return {"all": [
        {"field": "placing_player", "op": "eq", "value": "$source.controller"},
        {"field": "counter_kind_index", "op": "eq", "value": 1},
        {"field": "types", "op": "contains_any", "value": ["creature"]},
        {"field": "amount", "op": "gte", "value": 1},
    ]}


def counter_placement_blocking_effect():
    return {
        "op": "grant_declaration_restriction_until_end_of_turn",
        "card": EVENT_COUNTER_OBJECT,
        "restriction": OPPONENT_BLOCKING_PROHIBITION_KIND,
    }


def counter_placement_blocking_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if (target_schema is None and cost_schema is None and len(effects) == 1
            and FrozenMap(effects[0]) == FrozenMap(counter_placement_blocking_effect())
            and set(COUNTER_PLACEMENT_BLOCKING_MECHANICS) <= set(mechanic_ids)):
        return COUNTER_PLACEMENT_BLOCKING_DEPENDENCIES
    return ()


def counter_placement_blocking_program_dependencies(program):
    effects = tuple(program.effects)
    matches = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                    if effect.get("restriction") == OPPONENT_BLOCKING_PROHIBITION_KIND)
    if (not matches and COUNTER_PLACEMENT_BLOCKING_CAPABILITY not in program.capability_dependencies
            and COUNTER_PLACEMENT_BLOCKING_MECHANIC not in program.coverage):
        return ()
    required = counter_placement_blocking_node_capabilities(
        effects=effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage, cost_schema=program.cost_schema,
    )
    if (not required or effects != matches or program.handlers
            or (program.event, program.active_zone) != (COUNTER_PLACEMENT_EVENT, "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(counter_placement_blocking_condition())):
        raise ValueError("Opponent blocking must use the creature from the original committed counter event")
    return required


def is_closed_counter_placement_blocking_program(program):
    try:
        required = counter_placement_blocking_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
