from __future__ import annotations

"""Fixed turn-duration results for a sealed battlefield entry."""

from collections.abc import Mapping

from ..event_counter_reference import EVENT_ENTRY_OBJECT
from ..replacement.immutable import thaw_value
from ..saddle import SADDLE_CAPABILITY, SADDLE_OPERATION


ENTRY_PERMANENT_RESULT_CAPABILITY = "resolution.event_object.fixed_entry_permanent_result"
ENTRY_PERMANENT_RESULT_MECHANIC = "fixed-entry-permanent-result"
ENTRY_ANIMATION_OPERATION = "add_types_until_end_of_turn"


def entry_permanent_result_effect(operation):
    effect = {"op": operation, "card": EVENT_ENTRY_OBJECT}
    if operation == ENTRY_ANIMATION_OPERATION:
        return {**effect, "types": ["Artifact", "Creature"]}
    if operation == SADDLE_OPERATION:
        return effect
    raise ValueError("Unknown entry permanent result")


def entry_permanent_result_binding(event, condition, operation):
    if event == "permanent.enter.self":
        return operation == ENTRY_ANIMATION_OPERATION and condition is None
    if event != "permanent.enter":
        return False
    subtype = "mount" if operation == SADDLE_OPERATION else "vehicle"
    return thaw_value(condition) == {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "subtypes", "op": "contains_any", "value": [subtype]},
    ]}


def entry_permanent_result_capabilities(*, effects, target_schema, mechanic_ids):
    if ENTRY_PERMANENT_RESULT_MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:
        return ()
    operation = effects[0].get("op")
    if operation not in {ENTRY_ANIMATION_OPERATION, SADDLE_OPERATION}:
        return ()
    if thaw_value(effects[0]) != entry_permanent_result_effect(operation):
        return ()
    effect_capability = (
        SADDLE_CAPABILITY if operation == SADDLE_OPERATION
        else "continuous.resolution.fixed_characteristics_until_end_of_turn"
    )
    return (ENTRY_PERMANENT_RESULT_CAPABILITY, effect_capability)


def entry_permanent_result_runtime_dependencies(program, operation):
    def uses_entry(value):
        if isinstance(value, Mapping):
            return (value.get("op") == operation and value.get("card") == EVENT_ENTRY_OBJECT
                or any(uses_entry(child) for child in value.values()))
        return isinstance(value, (list, tuple)) and any(uses_entry(child) for child in value)

    coverage = getattr(program, "coverage", ())
    declared = ENTRY_PERMANENT_RESULT_MECHANIC in coverage and any(
        effect.get("op") == operation for effect in program.effects
    )
    if not declared and not uses_entry(program.effects):
        return ()
    required = entry_permanent_result_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=coverage,
    )
    if (not required or program.active_zone != "battlefield"
        or not entry_permanent_result_binding(program.event, program.event_condition, operation)):
        raise ValueError("Entry permanent results require the closed subtype occurrence")
    return required


def entry_animation_runtime_dependencies(program):
    return entry_permanent_result_runtime_dependencies(program, ENTRY_ANIMATION_OPERATION)
