from __future__ import annotations

"""Closed special effect shapes sharing generated-program promotion."""

from ..rules.self_return_capability_shapes import fixed_self_return_node_capabilities


def nonleaf_effect_program_is_closed(program,*,recognizers=()):
    from .waterbend_costs import closed_waterbend_spell_result
    if closed_waterbend_spell_result(program,recognizers=recognizers):return True
    from ..rules.cleave_shapes import fixed_cleave_program_is_closed
    if fixed_cleave_program_is_closed(program):return True
    from ..rules.overload_shapes import fixed_overload_program_is_closed
    if fixed_overload_program_is_closed(program):return True
    from ..rules.stack_spell_copy_shapes import fixed_stack_spell_copy_program_is_closed
    if fixed_stack_spell_copy_program_is_closed(program):return True
    from ..rules.paid_entry_copy_shapes import is_closed_paid_entry_copy_program
    if is_closed_paid_entry_copy_program(program):return True
    from ..rules.city_blessing_shapes import ascend_program_is_closed
    if ascend_program_is_closed(program):return True
    from ..random_discard import random_discard_node_capabilities
    if 'fixed-random-discard' in program.coverage:
        from ..rules.fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities
        required = fixed_optional_effect_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
        if required and set(required).issubset(program.capability_dependencies):
            return True
    from ..rules.linked_exile_return_shapes import linked_exile_return_node_capabilities
    from ..rules.resolution_condition_shapes import resolution_condition_node_capabilities
    from ..rules.affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
    from ..rules.fractional_player_result_shapes import half_life_node_capabilities, half_choice_node_capabilities
    for resolver in (fixed_self_return_node_capabilities,random_discard_node_capabilities,resolution_condition_node_capabilities,linked_exile_return_node_capabilities,
                     fixed_affected_player_sacrifice_node_capabilities, half_life_node_capabilities, half_choice_node_capabilities):
        required=resolver(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
        if required and set(required).issubset(program.capability_dependencies):return True
    return False
