from __future__ import annotations

"""Shared promotion boundaries for closed public graveyard movements."""

from ..rules.reanimation_capability_shapes import (
    fixed_target_reanimation_node_capabilities, ordered_graveyard_reanimation_runtime_dependencies,
    source_power_reanimation_runtime_dependencies,
)
from ..rules.graveyard_card_targets import targeted_graveyard_return_node_capabilities
from ..rules.graveyard_return_choices import each_graveyard_return_spell_runtime_dependencies


def is_closed_reanimation_effect_program(program):
    try:
        required = (ordered_graveyard_reanimation_runtime_dependencies(program)
                    or source_power_reanimation_runtime_dependencies(program)
                    or each_graveyard_return_spell_runtime_dependencies(program))
    except (ValueError, TypeError):
        return False
    if not required:
        required = fixed_target_reanimation_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
    return bool(required) and set(required)<=set(program.capability_dependencies)


def is_closed_graveyard_return_program(program):
    from ..rules.graveyard_return_choices import (
        graveyard_return_choice_runtime_dependencies, uses_different_name_graveyard_return,
    )

    if len(program.effects) == 1 and uses_different_name_graveyard_return(program.effects[0]):
        try:
            required = graveyard_return_choice_runtime_dependencies(program)
        except (TypeError, ValueError):
            return False
        return bool(required) and set(required).issubset(program.capability_dependencies)
    required=targeted_graveyard_return_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=(v for v in program.coverage if v in {'return-to-owner-hand','cr-115-targets'}))
    return bool(required) and set(required)<=set(program.capability_dependencies)
