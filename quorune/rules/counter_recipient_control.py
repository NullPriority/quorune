"""Fixed draw and life results for a committed creature counter occurrence."""

from collections.abc import Mapping

from ..counter_placement_event_model import COUNTER_EVENT_CAPABILITY, COUNTER_PLACEMENT_EVENT
from ..replacement.immutable import FrozenMap
from ..resolution_conditions import RESOLUTION_CONDITION_CAPABILITY, RESOLUTION_CONDITION_MECHANIC, RESOLUTION_CONDITION_OPERATION
from ..target_controller_information import occurrence_controller_reference
from .effect_shape_walkers import nested_effects


COUNTER_RECIPIENT_CONTROL_FIELD = "counter_recipient_control_result"
COUNTER_RECIPIENT_CONTROL_CAPABILITY = "trigger.counter_recipient.current_controller_branch"
COUNTER_RECIPIENT_CONTROL_MECHANIC = "counter-recipient-current-controller-result"
COUNTER_RECIPIENT_CONTROL_TEMPLATE = "counter-recipient-current-controller-result-v1"
COUNTER_RECIPIENT_CONTROL_MECHANICS = (RESOLUTION_CONDITION_MECHANIC, COUNTER_RECIPIENT_CONTROL_MECHANIC)
_COUNTER_RECIPIENT_CONTROL_DEPENDENCIES = (
    COUNTER_RECIPIENT_CONTROL_CAPABILITY, COUNTER_EVENT_CAPABILITY, RESOLUTION_CONDITION_CAPABILITY,
    "resolution.target_controller_information", "zone.draw.library_to_hand", "life.change.effect",
    "trigger.effect.fixed_event", "trigger.placement.apnap",
)


def counter_recipient_control_condition():
    return {"all": [
        {"field": "counter", "op": "eq", "value": "-1/-1"},
        {"field": "types", "op": "contains_any", "value": ["creature"]},
        {"field": "amount", "op": "gte", "value": 1},
    ]}


def counter_recipient_control_instructions():
    return tuple({
        "op": RESOLUTION_CONDITION_OPERATION, "player": "$controller",
        COUNTER_RECIPIENT_CONTROL_FIELD: branch, "recipient": occurrence_controller_reference(),
    } for branch in ("draw_if_controlled", "lose_life_if_other"))


def uses_counter_recipient_control(effect):
    return effect.get("op") == RESOLUTION_CONDITION_OPERATION and COUNTER_RECIPIENT_CONTROL_FIELD in effect


def counter_recipient_control_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    expected = counter_recipient_control_instructions()
    if (target_schema is None and cost_schema is None and len(effects) == len(expected)
            and all(FrozenMap(effect) == FrozenMap(template) for effect, template in zip(effects, expected, strict=True))
            and set(COUNTER_RECIPIENT_CONTROL_MECHANICS) <= set(mechanic_ids)):
        return _COUNTER_RECIPIENT_CONTROL_DEPENDENCIES
    return ()


def counter_recipient_control_program_dependencies(program):
    effects = tuple(program.effects)
    matches = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                    if uses_counter_recipient_control(effect))
    if (not matches and COUNTER_RECIPIENT_CONTROL_CAPABILITY not in program.capability_dependencies
            and COUNTER_RECIPIENT_CONTROL_MECHANIC not in program.coverage):
        return ()
    owned = counter_recipient_control_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage, cost_schema=program.cost_schema,
    )
    if (not owned or effects != matches or program.handlers
            or (program.event, program.active_zone) != (COUNTER_PLACEMENT_EVENT, "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(counter_recipient_control_condition())):
        raise ValueError("The control results require their exact committed creature counter occurrence")
    return owned


def counter_recipient_control_effects(effect, *, actor, stack_controller, active_seats):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", COUNTER_RECIPIENT_CONTROL_FIELD, "recipient"}
            or not uses_counter_recipient_control(effect) or effect["player"] != actor or actor != stack_controller
            or type(effect["recipient"]) is not str or not effect["recipient"] or effect["recipient"].startswith("$")):
        raise ValueError("The counter control result requires its resolving actor and materialized recipient controller")
    branch = effect[COUNTER_RECIPIENT_CONTROL_FIELD]
    recipient = effect["recipient"]
    if branch == "draw_if_controlled":
        return ({"op": "draw", "player": actor, "count": 1, "private": True},) if recipient == actor else ()
    if branch == "lose_life_if_other":
        return ({"op": "lose_life", "player": recipient, "amount": 1},) if recipient != actor and recipient in active_seats else ()
    raise ValueError("The counter control branch is unknown")

def is_closed_counter_recipient_control_program(program):
    try:
        required = counter_recipient_control_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
