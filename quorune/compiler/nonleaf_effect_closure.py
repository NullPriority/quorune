from __future__ import annotations

"""Closed special effect shapes sharing generated-program promotion."""

from ..rules.self_return_capability_shapes import fixed_self_return_node_capabilities


def _is_closed_fixed_library_selection_program(program):
    from ..rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities

    required = set(fixed_library_selection_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    ))
    return bool(required) and required.issubset(program.capability_dependencies)


def _is_closed_variable_library_selection_program(program):
    if len(program.effects) != 1 or program.effects[0].get("op") != "variable_library_selection":
        return False
    if "variable-library-selection" not in program.coverage:
        return False
    effect = program.effects[0]
    try:
        from ..variable_library_selection_model import variable_library_selection_payload_dependencies
        from ..card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies

        required = set(variable_library_selection_payload_dependencies(effect))
        required.update(typed_result_amount_runtime_dependencies(
            effect.get("look_count"), mechanics=tuple(program.coverage),
        ))
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and required.issubset(program.capability_dependencies)


def direct_effect_program_is_closed(program):
    """Recognize direct effect owners before composing instructions."""
    from ..rules.paid_sacrifice_copy import is_closed_paid_sacrifice_copy_program
    if is_closed_paid_sacrifice_copy_program(program):
        return True
    from ..rules.repeated_apnap_payment import payment_round_program_dependencies

    try:
        if payment_round_program_dependencies(program):
            return True
    except (TypeError, ValueError, KeyError):
        pass
    from ..rules.cast_x_zone_searches import is_closed_cast_x_zone_search_program
    from ..rules.named_searches import is_closed_named_zone_search_program
    from ..rules.owned_commander_battlefield_return import generated_commander_return_is_closed
    from ..rules.owned_commander_amount import is_closed_owned_commander_amount_program
    from ..rules.void_counter_play import is_closed_void_counter_play_program
    from ..rules.target_graveyard_escape import is_closed_target_graveyard_escape_program
    from ..rules.paid_artifact_library_search import is_closed_paid_artifact_search_program
    from ..rules.loyalty_attachment import is_closed_loyalty_attachment_program
    from ..rules.paid_creature_library_search import is_closed_paid_creature_search_program
    if (is_closed_paid_artifact_search_program(program) or is_closed_paid_creature_search_program(program)
            or is_closed_cast_x_zone_search_program(program) or is_closed_loyalty_attachment_program(program)):
        return True
    if (is_closed_named_zone_search_program(program) or generated_commander_return_is_closed(program)
            or is_closed_void_counter_play_program(program) or is_closed_target_graveyard_escape_program(program)
            or is_closed_owned_commander_amount_program(program)):
        return True
    from ..rules.attachment_action_capability_shapes import (
        is_closed_fixed_source_equipment_activation_program, is_closed_targeted_equipment_attachment_program,
    )
    from ..rules.cast_cascade_grants import is_closed_cast_cascade_program
    from ..rules.next_spell_payment import is_closed_next_spell_payment_program
    if (is_closed_next_spell_payment_program(program) or is_closed_cast_cascade_program(program)
            or is_closed_fixed_source_equipment_activation_program(program)
            or is_closed_targeted_equipment_attachment_program(program)):
        return True
    return False


def effect_program_is_closed(program, *, recognizers, recursive_is_closed, composed_is_closed):
    """Apply the shared promotion policy to a complete effect program."""
    from ..rules.cast_spell_suspend import is_closed_cast_spell_suspend_program
    if is_closed_cast_spell_suspend_program(program):
        return True
    from ..card_programs.runtime_requirements import is_closed_target_death_return_program
    from ..rules.fixed_counter_movement import is_closed_fixed_counter_movement_program
    from ..rules.departure_counter_transfer import is_closed_departure_counter_transfer_program
    from ..rules.counter_recipient_control import is_closed_counter_recipient_control_program
    from ..rules.counter_placement_blocking import is_closed_counter_placement_blocking_program
    from ..rules.controlled_arrival_proliferate import is_closed_controlled_arrival_proliferate_program
    from ..rules.linked_color_cast_exile import is_closed_linked_color_cast_exile
    from ..rules.source_power_opponent_damage import is_closed_source_power_opponent_damage_program

    if (is_closed_fixed_counter_movement_program(program) or is_closed_target_death_return_program(program)
            or is_closed_departure_counter_transfer_program(program)
            or is_closed_counter_placement_blocking_program(program)
            or is_closed_counter_recipient_control_program(program)
            or is_closed_controlled_arrival_proliferate_program(program)
            or is_closed_linked_color_cast_exile(program)
            or is_closed_source_power_opponent_damage_program(program)):
        return True
    from ..rules.colored_hand_credit import COLORED_HAND_CREDIT
    from .colored_hand_credit_template import closed_colored_hand_credit_spell

    if COLORED_HAND_CREDIT in (program.cost_schema or {}):
        return closed_colored_hand_credit_spell(program, body_is_closed=recursive_is_closed)

    from ..rules.paid_revealed_creature_results import is_closed_paid_revealed_creature_result
    if is_closed_paid_revealed_creature_result(program):
        return True

    from ..rules.kicked_artifact_entry import has_kicked_artifact_entry_limit, is_closed_kicked_artifact_entry_program
    if has_kicked_artifact_entry_limit(program.target_schema):
        return is_closed_kicked_artifact_entry_program(program, body_is_closed=recursive_is_closed)
    from ..rules.entry_x_support import closed_entry_x_support_program, has_entry_x_support
    if has_entry_x_support(program.target_schema):
        return closed_entry_x_support_program(program, body_is_closed=recursive_is_closed)
    from ..rules.entry_x_graveyard_target import closed_entry_x_graveyard_target_program, has_entry_x_target_limit
    if has_entry_x_target_limit(program.target_schema):
        return closed_entry_x_graveyard_target_program(program, body_is_closed=recursive_is_closed)
    from ..rules.announced_mana_value_target import has_announced_mana_value_limit
    if has_announced_mana_value_limit(program.target_schema):
        from .announced_mana_value_target import closed_announced_mana_value_target_program
        return closed_announced_mana_value_target_program(program, body_is_closed=recursive_is_closed)

    from ..rules.graveyard_exile_branches import is_closed_graveyard_exile_branch_spell
    from ..rules.graveyard_exile_tokens import is_closed_graveyard_exile_token_activation
    from ..rules.graveyard_aura_entry import is_closed_graveyard_aura_entry_program

    if (is_closed_graveyard_aura_entry_program(program) or is_closed_graveyard_exile_branch_spell(program)
            or is_closed_graveyard_exile_token_activation(program)):
        return True
    from ..rules.optional_sacrifice_stats import is_closed_optional_sacrifice_stat_program
    if is_closed_optional_sacrifice_stat_program(program):return True
    from ..rules.recover import is_closed_recover_program
    if is_closed_recover_program(program):return True
    from ..rules.paradigm import is_closed_paradigm_program
    if 'paradigm' in program.coverage:return is_closed_paradigm_program(program, recursive_is_closed)
    from ..rules.finite_damage_redirection import FINITE_DAMAGE_REDIRECTION_MECHANIC, is_closed_finite_damage_redirection

    if FINITE_DAMAGE_REDIRECTION_MECHANIC in program.coverage:
        return is_closed_finite_damage_redirection(program)
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
    from ..rules.permanent_resolution_choices import permanent_resolution_choice_capabilities

    chosen = permanent_resolution_choice_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanics=program.coverage, cost_schema=program.cost_schema,
    )
    if chosen is not None:
        return bool(chosen) and set(chosen) <= set(program.capability_dependencies)
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
    from ..rules.die_roll_draw import is_closed_controller_hand_count_draw_and_size_program
    if is_closed_controller_hand_count_draw_and_size_program(program):return True
    from ..draw_redirect_model import temporary_draw_redirect_node_capabilities
    from ..rules.next_draw_replacement_shapes import next_draw_replacement_node_capabilities
    from ..rules.temporary_cast_restrictions import temporary_cast_node_capabilities

    from ..rules.next_personal_turn_protection import next_personal_turn_protection_runtime_dependencies
    try:
        protection = next_personal_turn_protection_runtime_dependencies(program)
    except ValueError:
        return False
    if protection and set(protection) <= set(program.capability_dependencies):
        return True

    shape = dict(effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
    player_rule = (temporary_cast_node_capabilities(**shape) or temporary_draw_redirect_node_capabilities(**shape)
                   or next_draw_replacement_node_capabilities(**shape))
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
    from ..rules.linked_exile_activations import is_closed_linked_exile_activation_program

    if is_closed_linked_exile_activation_program(program):
        return True
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
