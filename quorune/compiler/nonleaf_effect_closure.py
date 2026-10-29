from __future__ import annotations

"""Closed special effect shapes sharing generated-program promotion."""

from ..rules.self_return_capability_shapes import fixed_self_return_node_capabilities


def effect_program_is_closed(program, *, recognizers, recursive_is_closed, composed_is_closed):
    """Apply the shared promotion policy to a complete effect program."""
    if 'cipher_spell_resolution' in program.coverage:
        from dataclasses import replace
        from ..cipher_model import CIPHER_CAPABILITY, CIPHER_HANDLER
        if (CIPHER_CAPABILITY not in program.capability_dependencies or not program.effects
                or program.effects[-1] != {'op': 'encode_cipher'}):
            return False
        body = replace(program, effects=list(program.effects[:-1]),
            handlers=[h for h in program.handlers if h.get('handler_id') != CIPHER_HANDLER],
            coverage=[value for value in program.coverage if value != 'cipher_spell_resolution'])
        return bool(body.effects) and recursive_is_closed(body)
    from ..rules.spell_self_exile import MECHANIC as SELF_EXILE_MECHANIC
    from .spell_self_exile import closed_spell_self_exile_program
    from .declared_effect_amounts import closed_additional_cost_x_result
    if SELF_EXILE_MECHANIC in program.coverage:
        return closed_spell_self_exile_program(program,body_is_closed=recursive_is_closed,
            price_is_closed=closed_additional_cost_x_result)
    from .permanent_additional_cost_nodes import is_closed_permanent_additional_cost_program
    if is_closed_permanent_additional_cost_program(program):
        return True
    if nonleaf_effect_program_is_closed(program,recognizers=recognizers):
        return True
    if composed_is_closed(program,component_recognizers=recognizers):
        return True
    return any(recognizer(program) for recognizer in recognizers)


def nonleaf_effect_program_is_closed(program,*,recognizers=()):
    from .fixed_sacrifice_result_promotion import closed_fixed_sacrifice_spell_result
    if closed_fixed_sacrifice_spell_result(program,recognizers=recognizers):return True
    from .nonmana_equip_nodes import is_closed_nonmana_equip_program
    if is_closed_nonmana_equip_program(program):return True
    from ..rules.temporary_cast_restrictions import temporary_cast_node_capabilities
    cast_rule=temporary_cast_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    if cast_rule and set(cast_rule)<=set(program.capability_dependencies):return True
    from ..rules.random_graveyard_returns import random_graveyard_return_node_capabilities
    random_return=random_graveyard_return_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    if random_return and set(random_return)<=set(program.capability_dependencies):return True
    from .declared_effect_amounts import closed_additional_cost_x_result
    if closed_additional_cost_x_result(program,recognizers=recognizers):return True
    from .random_discard_spell_costs import closed_random_discard_spell_result
    if closed_random_discard_spell_result(program,recognizers=recognizers):return True
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
