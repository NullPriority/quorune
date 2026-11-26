"""Keep both faction results bound to the exact event and entry choice."""

from collections.abc import Mapping

from ..entry_designations import (
    ENTRY_DESIGNATION_CAPABILITY, ENTRY_DESIGNATION_HANDLER_ID, ENTRY_FACTION_CAPABILITY,
)
from .faction_trigger_model import (
    FACTION_CONDITION_FIELD, FACTION_DRAW_CAPABILITY, FACTION_RAD_CAPABILITY, FACTION_RESULT_FIELD,
    faction_branch_capabilities, faction_condition, faction_effects, faction_event,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key


def faction_branch_for_program(program):
    condition = thaw_value(program.event_condition)
    effects = tuple(thaw_value(effect) for effect in program.effects)
    if any(type(effect.get("schema_version")) is not int for effect in effects):
        raise ValueError("Faction results require exactly versioned instructions")
    for choice in ("Brotherhood", "Enclave"):
        if (program.event == faction_event(choice) and condition == faction_condition(choice)
                and effects == faction_effects(choice)):
            if (program.active_zone != "battlefield" or program.target_schema is not None
                    or program.cost_schema is not None or program.trigger_limit is not None or program.handlers):
                raise ValueError("Faction triggers require their original untargeted battlefield instructions")
            return choice
    raise ValueError("Faction triggers require the exact source entry choice, occurrence and result")


def faction_trigger_runtime_dependencies(program):
    is_branch = set(program.capability_dependencies).intersection((FACTION_DRAW_CAPABILITY, FACTION_RAD_CAPABILITY))
    has_choices = any(
        isinstance(handler, Mapping) and handler.get("designation") == "faction" for handler in program.handlers
    )
    if is_branch or contains_nested_key(program.effects, FACTION_RESULT_FIELD) or _mentions_faction_condition(program.event_condition):
        return faction_branch_capabilities(faction_branch_for_program(program))
    if not has_choices and ENTRY_FACTION_CAPABILITY not in program.capability_dependencies:
        return ()
    descriptor = {"handler_id": ENTRY_DESIGNATION_HANDLER_ID, "schema_version": 1,
                  "event": "zone.change", "designation": "faction"}
    if (tuple(thaw_value(value) for value in program.handlers) != (descriptor,)
            or type(program.handlers[0]["schema_version"]) is not int
            or (program.event, program.active_zone) != ("zone.change", "all")
            or program.effects or program.event_condition is not None or program.target_schema is not None
            or program.cost_schema is not None or program.trigger_limit is not None):
        raise ValueError("Faction entry requires its single intrinsic designation handler")
    return ENTRY_DESIGNATION_CAPABILITY, ENTRY_FACTION_CAPABILITY


def _mentions_faction_condition(value):
    if isinstance(value, Mapping):
        return value.get("field") == FACTION_CONDITION_FIELD or any(
            _mentions_faction_condition(child) for child in value.values()
        )
    return isinstance(value, (tuple, list)) and any(_mentions_faction_condition(child) for child in value)
