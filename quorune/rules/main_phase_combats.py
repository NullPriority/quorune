from __future__ import annotations

from .main_phase_combat_model import (
    ATTACK_UNTAP_CAPABILITY, ATTACK_UNTAP_FIELD, IMMEDIATE_COMBAT_CAPABILITY, MAIN_COMBAT_CAPABILITY,
    MAIN_COMBAT_FIELD, MainPhaseCombatInstruction, main_phase_combat_instruction,
)
from .effect_shape_walkers import nested_effects


DEPENDENCIES = (
    MAIN_COMBAT_CAPABILITY, ATTACK_UNTAP_CAPABILITY, "condition.history.fixed_public_query",
    "permanent.tap_state.fixed_set", "trigger.placement.apnap",
)


IMMEDIATE_COMBAT_DEPENDENCIES = (
    IMMEDIATE_COMBAT_CAPABILITY, "condition.history.fixed_public_query", "permanent.tap_state.fixed_set",
)


def main_phase_combat_payload_dependencies(effect):
    if MAIN_COMBAT_FIELD in effect:
        instruction = main_phase_combat_instruction(effect)
        if instruction is MainPhaseCombatInstruction.COMBAT_THEN_MAIN_IMMEDIATE_UNTAP:
            return IMMEDIATE_COMBAT_DEPENDENCIES
        return DEPENDENCIES
    if ATTACK_UNTAP_FIELD in effect:
        raise ValueError("Turn-attacker untap is only executable as the actual delayed result")
    return ()


def main_phase_combat_node_capabilities(effects, target_schema, cost_schema):
    if not any(MAIN_COMBAT_FIELD in effect or ATTACK_UNTAP_FIELD in effect for effect in effects):
        return None
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:
        return main_phase_combat_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def main_phase_combat_program_dependencies(program):
    if not any(MAIN_COMBAT_FIELD in effect or ATTACK_UNTAP_FIELD in effect
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = main_phase_combat_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers):
        raise ValueError("The main-phase combat schedule requires its complete untargeted spell")
    return required


def is_closed_main_phase_combat_program(program):
    try:
        required = main_phase_combat_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
