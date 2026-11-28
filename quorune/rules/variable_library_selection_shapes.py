from __future__ import annotations

from ..variable_library_selection_model import (
    VARIABLE_LIBRARY_SELECTION_MECHANIC, variable_library_selection_payload_dependencies,
    uses_actual_mana_spent_library_selection, uses_devotion_library_win,
)


def variable_library_selection_node_capabilities(*,effects,target_schema,mechanic_ids):
    if VARIABLE_LIBRARY_SELECTION_MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    try:required=variable_library_selection_payload_dependencies(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    return required if effects[0].get('player')=='$controller' else ()


def actual_mana_spent_library_runtime_dependencies(program):
    if (
        (program.event, program.active_zone) != ("resolve", "stack")
        or not program.ability_id.startswith("spell:") or program.handlers
        or len(program.effects) != 1 or not uses_actual_mana_spent_library_selection(program.effects[0])
    ):
        raise ValueError("Actual payment library selection requires the original ordinary spell")
    required = variable_library_selection_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Actual payment selection requires the original paid receipt and complete private result")
    return required


def is_closed_variable_library_selection_program(program):
    required=variable_library_selection_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    if any(uses_devotion_library_win(effect) for effect in program.effects):
        try:
            required = devotion_library_win_runtime_dependencies(program)
        except (TypeError, ValueError):
            return False
    return bool(required) and set(required)<=set(program.capability_dependencies)


def devotion_library_win_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(uses_devotion_library_win(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or len(program.effects) != 1 or not uses_devotion_library_win(program.effects[0])
            or program.handlers or program.target_schema is not None or program.cost_schema is not None
            or program.event_condition is not None):
        raise ValueError("Devotion library selection requires one complete self-entry trigger")
    return variable_library_selection_payload_dependencies(program.effects[0])
