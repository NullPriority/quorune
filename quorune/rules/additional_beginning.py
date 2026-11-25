"""Trust only the closed postcombat additional-beginning trigger scope."""

from ..additional_beginning_model import (
    ADDITIONAL_BEGINNING_CAPABILITY,
    ADDITIONAL_BEGINNING_OPERATION,
    additional_beginning_condition,
    decode_additional_beginning_instruction,
)
from ..replacement.immutable import thaw_value


def additional_beginning_runtime_dependencies(program):
    effects = tuple(program.effects)
    if not any(effect.get("op") == ADDITIONAL_BEGINNING_OPERATION for effect in effects):
        return ()
    condition = thaw_value(program.event_condition)
    if (
        program.event != "step.begin"
        or program.active_zone != "battlefield"
        or program.target_schema is not None
        or len(effects) != 1
        or condition not in (additional_beginning_condition(), additional_beginning_condition(enchanted=True))
    ):
        raise ValueError("Additional beginning requires one closed postcombat trigger")
    decode_additional_beginning_instruction(effects[0])
    return (ADDITIONAL_BEGINNING_CAPABILITY, "trigger.placement.apnap")


def is_closed_additional_beginning_program(program):
    try:
        required = additional_beginning_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
