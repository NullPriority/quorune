from __future__ import annotations

"""Closed special effect shapes sharing generated-program promotion."""

from ..rules.self_return_capability_shapes import fixed_self_return_node_capabilities


def effect_program_is_closed(program, *, recognizers, recursive_is_closed, composed_is_closed):
    """Apply the shared promotion policy to a complete effect program."""
    from ..rules.colored_hand_credit import COLORED_HAND_CREDIT
    from .colored_hand_credit_template import closed_colored_hand_credit_spell

    if COLORED_HAND_CREDIT in (program.cost_schema or {}):
        return closed_colored_hand_credit_spell(program, body_is_closed=recursive_is_closed)

    from ..rules.entry_x_graveyard_target import closed_entry_x_graveyard_target_program, has_entry_x_target_limit
    if has_entry_x_target_limit(program.target_schema):
        return closed_entry_x_graveyard_target_program(program, body_is_closed=recursive_is_closed)
    from ..rules.announced_mana_value_target import has_announced_mana_value_limit
    if has_announced_mana_value_limit(program.target_schema):
        from .announced_mana_value_target import closed_announced_mana_value_target_program
        return closed_announced_mana_value_target_program(program, body_is_closed=recursive_is_closed)

    from ..rules.graveyard_exile_branches import is_closed_graveyard_exile_branch_spell
    from ..rules.graveyard_exile_tokens import is_closed_graveyard_exile_token_activation

    if is_closed_graveyard_exile_branch_spell(program) or is_closed_graveyard_exile_token_activation(program):
        return True
    from ..rules.optional_sacrifice_stats import is_closed_optional_sacrifice_stat_program
    if is_closed_optional_sacrifice_stat_program(program):return True
    from ..rules.recover import is_closed_recover_program
    if is_closed_recover_program(program):return True
    from ..rules.paradigm import is_closed_paradigm_program
    if 'paradigm' in program.coverage:return is_closed_paradigm_program(program, recursive_is_closed)
    from ..rules.current_life_effect_amounts import is_closed_current_life_program
    if is_closed_current_life_program(program):return True
    from ..rules.turn_life_loss_recovery import is_closed_turn_life_loss_program
    if is_closed_turn_life_loss_program(program):return True
    from ..rules.per_player_query_life import is_closed_per_player_query_life_program
    if is_closed_per_player_query_life_program(program):return True
    from ..rules.per_player_query_damage import is_closed_per_player_query_damage_program
    if is_closed_per_player_query_damage_program(program):return True
    from ..rules.per_player_life_damage import is_closed_per_player_life_damage_program
    if is_closed_per_player_life_damage_program(program):return True
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
    from ..rules.spell_self_exile import TERMINAL_SPELL_MOVE_MECHANICS
    from .spell_self_exile import closed_terminal_spell_move_program
    from .declared_effect_amounts import closed_additional_cost_x_result
    if TERMINAL_SPELL_MOVE_MECHANICS.intersection(program.coverage):
        return closed_terminal_spell_move_program(program,body_is_closed=recursive_is_closed,
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
    from ..rules.temporary_keyword_removal import is_closed_chosen_keyword_removal_program
    from ..rules.targeted_graveyard_exile import is_closed_targeted_graveyard_exile_program
    if is_closed_chosen_keyword_removal_program(program) or is_closed_targeted_graveyard_exile_program(program):
        return True
    from ..rules.event_player_modifiers import is_closed_event_player_modifier_program
    if is_closed_event_player_modifier_program(program):return True
    from ..rules.target_player_query_amounts import is_closed_target_player_query_program
    if is_closed_target_player_query_program(program):return True
    from ..rules.colored_hand_reveals import is_closed_colored_hand_reveal_program
    if is_closed_colored_hand_reveal_program(program):return True
    from ..saddle import is_closed_saddle_program
    if is_closed_saddle_program(program):return True
    from .fixed_sacrifice_result_promotion import closed_alternative_damage_death_exile_result, closed_fixed_sacrifice_spell_result
    if (closed_fixed_sacrifice_spell_result(program, recognizers=recognizers)
            or closed_alternative_damage_death_exile_result(program, recognizers=recognizers)):
        return True
    from .nonmana_equip_nodes import is_closed_nonmana_equip_program
    if is_closed_nonmana_equip_program(program):return True
    from ..draw_redirect_model import temporary_draw_redirect_node_capabilities
    from ..rules.temporary_cast_restrictions import temporary_cast_node_capabilities

    shape = dict(effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
    player_rule = temporary_cast_node_capabilities(**shape) or temporary_draw_redirect_node_capabilities(**shape)
    if player_rule and set(player_rule) <= set(program.capability_dependencies):
        return True
    from ..rules.random_graveyard_returns import random_graveyard_return_node_capabilities
    random_return=random_graveyard_return_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    if random_return and set(random_return)<=set(program.capability_dependencies):return True
    from .declared_effect_amounts import closed_additional_cost_x_result
    if closed_additional_cost_x_result(program,recognizers=recognizers):return True
    from .random_discard_spell_costs import closed_random_discard_spell_result
    if closed_random_discard_spell_result(program,recognizers=recognizers):return True
    from .waterbend_costs import closed_waterbend_spell_result
    if closed_waterbend_spell_result(program,recognizers=recognizers):return True
    from ..rules.spell_text_variant_shapes import fixed_variant_program_is_closed
    from ..spell_text_variants import SpellTextVariantKind
    if any(fixed_variant_program_is_closed(program,kind) for kind in SpellTextVariantKind):return True
    from ..rules.event_outcome_shapes import event_outcome_program_is_closed
    if event_outcome_program_is_closed(program): return True
    from ..rules.stack_spell_copy_shapes import fixed_stack_spell_copy_program_is_closed
    if fixed_stack_spell_copy_program_is_closed(program):return True
    from ..rules.linked_clue_copy import is_closed_linked_clue_copy_program
    if is_closed_linked_clue_copy_program(program):
        return True
    from ..rules.stack_target_change_shapes import single_stack_target_change_program_is_closed
    if single_stack_target_change_program_is_closed(program):return True
    from ..face_up_effect_model import face_up_node_capabilities
    from ..card_programs.admission import program_has_declared_complete_source_admission
    face_up=face_up_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanics=set(program.coverage)-{'generated_oracle_ir','spell_resolution','activated_ability'})
    if face_up and program_has_declared_complete_source_admission(program) and set(face_up).issubset(program.capability_dependencies):return True
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
    from ..rules.third_life_shapes import third_life_node_capabilities
    from ..rules.prepared_creature_power_damage_shapes import prepared_creature_power_damage_capabilities
    from ..rules.conditional_target_preparation import conditional_prepared_power_capabilities
    conditional = resolution_condition_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage, cost_schema=program.cost_schema,
    )
    if conditional and set(conditional).issubset(program.capability_dependencies):
        return True
    for resolver in (fixed_self_return_node_capabilities,random_discard_node_capabilities,linked_exile_return_node_capabilities,
                     fixed_affected_player_sacrifice_node_capabilities, half_life_node_capabilities, half_choice_node_capabilities, third_life_node_capabilities,
                     prepared_creature_power_damage_capabilities, conditional_prepared_power_capabilities):
        required=resolver(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
        if required and set(required).issubset(program.capability_dependencies):return True
    return False
