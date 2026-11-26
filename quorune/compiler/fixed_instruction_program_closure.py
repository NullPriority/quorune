from __future__ import annotations

"""Reviewed closure of separately owned fixed rule-action instructions."""

from ..rules.artifact_sacrifice_return import is_closed_artifact_sacrifice_return_program
from ..rules.extra_turn_shapes import is_closed_fixed_extra_turn_program
from ..rules.scheduled_turn_skip_shapes import is_closed_scheduled_turn_skip_program
from ..rules.earthbend_shapes import is_closed_earthbend_program
from ..rules.suspect_shapes import is_closed_suspect_program
from ..rules.combat_removal_shapes import is_closed_combat_removal_program
from ..rules.connive_capability_shapes import is_closed_fixed_connive_program
from ..rules.endure_shapes import is_closed_fixed_endure_program
from ..rules.blight_shapes import is_closed_fixed_blight_program
from ..rules.goad_capability_shapes import is_closed_fixed_goad_program
from ..rules.exploit_capability_shapes import is_closed_exploit_instruction_program
from ..rules.created_token_counter_shapes import is_closed_created_token_counter_program
from ..rules.token_creation_reflexive import is_closed_token_creation_reflexive_program
from ..rules.incubate_capability_shapes import is_closed_incubate_program
from ..rules.discover_capability_shapes import is_closed_discover_program
from ..rules.milled_creature_tokens import is_closed_milled_creature_token_program
from ..rules.committed_mill_cards import is_closed_committed_mill_card_program
from ..rules.ordered_type_sacrifice import is_closed_ordered_type_sacrifice_program
from ..rules.targeted_discard_spell_copy import is_closed_targeted_discard_copy_program
from ..rules.variable_library_selection_shapes import is_closed_variable_library_selection_program
from ..rules.reveal_until_library_selection_shapes import is_closed_reveal_until_library_selection_program
from ..rules.detain import is_closed_permanent_action_prohibition_program
from ..rules.distinct_sacrifices import is_closed_distinct_sacrifice_program
from ..drawing.captured_hand import is_closed_captured_draw_cast_program
from ..bounded_hand_cast_model import is_closed_bounded_hand_cast_program
from ..dungeon_progress import is_closed_venture_program
from ..initiative_model import is_closed_initiative_program
from ..rules.monarch_capability_shapes import is_closed_fixed_monarch_program
from ..rules.library_result_capability_shapes import is_closed_mana_contribution_draw_program
from ..rules.owner_library_position_shapes import is_closed_draw_source_owner_top_program
from ..rules.loot_transform_graveyard import is_closed_loot_transform_or_graveyard_cast_program
from ..rules.controller_private_top_exile_model import is_closed_controller_private_top_exile_program
from ..rules.controller_top_exile_activation import is_closed_controller_top_exile_program
from ..rules.opponent_library_exile_triggers import is_closed_draw_then_opponent_exile_program
from ..rules.linked_exile_return_shapes import (
    is_closed_controller_library_top_hand_program, is_closed_linked_fading_activation_program,
)
from ..rules.linked_loyalty_exile_shapes import is_closed_linked_loyalty_activation_program
from ..rules.next_spell_payment import (
    is_closed_turn_color_spell_protection_program, is_closed_turn_spell_source_color_protection_program,
)


def is_closed_fixed_instruction_program(program):
    from ..rules.occurrence_object_results import is_closed_phase_out_program
    if is_closed_phase_out_program(program):
        return True
    from ..rules.shared_type_library import is_closed_shared_type_library_program
    if is_closed_shared_type_library_program(program):
        return True
    from ..rules.fixed_hand_putback import is_closed_fixed_putback_program
    if is_closed_fixed_putback_program(program):return True
    from ..rules.linked_hand_mana import is_closed_linked_hand_imprint
    if is_closed_linked_hand_imprint(program):
        return True
    from ..rules.draw_step_drawing_settlement import is_closed_draw_step_settlement_program
    if is_closed_draw_step_settlement_program(program):
        return True
    from ..rules.hand_library_redraw import is_closed_hand_library_redraw_program
    if is_closed_hand_library_redraw_program(program):return True
    from ..rules.referenced_damage_shapes import is_closed_referenced_damage_program
    if is_closed_referenced_damage_program(program):return True
    from ..rules.source_self_entry_counters import is_closed_source_self_entry_counter_program
    if is_closed_source_self_entry_counter_program(program):return True
    from ..rules.self_zone_move_followups import is_closed_self_entry_followup_program
    if is_closed_self_entry_followup_program(program):return True
    from ..rules.event_optional_draw_shapes import is_closed_event_optional_draw_program
    from ..rules.source_sacrifice_shapes import is_closed_fixed_source_sacrifice_program
    from ..rules.target_all_counter_removal import is_closed_target_all_counter_variant_program
    from ..rules.chosen_counter_removal import is_closed_chosen_counter_removal_program
    from ..rules.chosen_counter_placement import is_closed_chosen_counter_placement_program
    from ..rules.captured_draw_keyword_grant import is_closed_captured_draw_keyword_grant_program
    from ..rules.optional_target_results import is_closed_optional_target_result_program
    from ..rules.target_land_type import is_closed_target_land_type_program
    from ..rules.graveyard_recycling import is_closed_graveyard_recycling_program
    from ..rules.target_library_inspection import is_closed_target_library_inspection_program
    from ..rules.fixed_damage_roles import is_closed_fixed_damage_role_program
    from ..rules.optional_source_damage import is_closed_optional_source_damage_program
    from ..rules.collective_source_damage import is_closed_collective_source_damage_program
    from ..rules.protected_attack_payments import is_closed_protected_attack_payment_program
    from ..rules.opponent_discard_results import is_closed_opponent_discard_program
    from ..rules.fame_fortune_choices import is_closed_fame_fortune_program
    return any(recognizer(program) for recognizer in (
        is_closed_event_optional_draw_program,
        is_closed_mana_contribution_draw_program,
        is_closed_draw_source_owner_top_program,
        is_closed_controller_top_exile_program,
        is_closed_controller_private_top_exile_program,
        is_closed_loot_transform_or_graveyard_cast_program,
        is_closed_draw_then_opponent_exile_program,
        is_closed_fixed_source_sacrifice_program,
        is_closed_target_all_counter_variant_program,
        is_closed_chosen_counter_removal_program,
        is_closed_chosen_counter_placement_program,
        is_closed_captured_draw_keyword_grant_program,
        is_closed_optional_target_result_program,
        is_closed_target_land_type_program,
        is_closed_graveyard_recycling_program,
        is_closed_target_library_inspection_program,
        is_closed_fixed_damage_role_program,
        is_closed_optional_source_damage_program,
        is_closed_collective_source_damage_program,
        is_closed_protected_attack_payment_program,
        is_closed_opponent_discard_program,
        is_closed_fame_fortune_program,
        is_closed_artifact_sacrifice_return_program,
        is_closed_created_token_counter_program,
        is_closed_token_creation_reflexive_program,
        is_closed_distinct_sacrifice_program,
        is_closed_targeted_discard_copy_program,
        is_closed_captured_draw_cast_program,
        is_closed_bounded_hand_cast_program,
        is_closed_venture_program,
        is_closed_initiative_program,
        is_closed_fixed_monarch_program,
        is_closed_linked_fading_activation_program,
        is_closed_controller_library_top_hand_program,
        is_closed_linked_loyalty_activation_program,
        is_closed_turn_color_spell_protection_program,
        is_closed_turn_spell_source_color_protection_program,
        is_closed_incubate_program,
        is_closed_fixed_endure_program,
        is_closed_fixed_blight_program,
        is_closed_discover_program,
        is_closed_milled_creature_token_program,
        is_closed_committed_mill_card_program,
        is_closed_ordered_type_sacrifice_program,
        is_closed_permanent_action_prohibition_program,
        is_closed_variable_library_selection_program,
        is_closed_reveal_until_library_selection_program,
        is_closed_fixed_extra_turn_program, is_closed_fixed_connive_program, is_closed_fixed_goad_program,
        is_closed_scheduled_turn_skip_program,
        is_closed_earthbend_program,
        is_closed_suspect_program,
        is_closed_combat_removal_program,
        is_closed_exploit_instruction_program,
    ))
