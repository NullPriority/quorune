"""Validate the complete creature-tap price and every paid mode independently."""

from collections.abc import Mapping

from ..escalate_tap_model import ESCALATE_TAP_CAPABILITY, ESCALATE_TAP_FIELD, EscalateTapModeSpec
from .tap_additional_cost import TAP_ADDITIONAL_COST_CAPABILITY


def escalate_tap_capabilities(*, effects, target_schema, mechanics, cost_schema, modal_resolver):
    if (not isinstance(cost_schema, Mapping) or set(cost_schema) != {ESCALATE_TAP_FIELD}
            or effects or "fixed-nonrepeating-modal" not in mechanics):
        return ()
    try:
        policy = EscalateTapModeSpec.from_dict(cost_schema[ESCALATE_TAP_FIELD])
        policy.validate_schema(target_schema)
    except (TypeError, ValueError):
        return ()
    owned = modal_resolver(effects=effects, target_schema=target_schema, mechanics=mechanics)
    return (*owned, TAP_ADDITIONAL_COST_CAPABILITY, ESCALATE_TAP_CAPABILITY) if owned else ()


def escalate_tap_runtime_dependencies(program):
    schema = program.cost_schema or {}
    if ESCALATE_TAP_FIELD not in schema and ESCALATE_TAP_CAPABILITY not in program.capability_dependencies:
        return ()
    if (set(schema) != {ESCALATE_TAP_FIELD} or program.effects or program.handlers
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.event_condition is not None):
        raise ValueError("Escalate tap needs its complete resolving spell")
    EscalateTapModeSpec.from_dict(schema[ESCALATE_TAP_FIELD]).validate_schema(program.target_schema)
    return (TAP_ADDITIONAL_COST_CAPABILITY, ESCALATE_TAP_CAPABILITY)
