from __future__ import annotations

"""Canonical dispatch to independently owned typed effect capability shapes."""

from typing import Any,Mapping,Sequence
from .control_capability_shapes import fixed_control_node_capabilities
from .city_blessing_shapes import ascend_spell_node_capabilities
from .public_event_counter_set_shapes import public_event_counter_set_node_capabilities
from .extra_turn_shapes import fixed_extra_turn_node_capabilities
from .whole_hand_discard_shapes import unchosen_hand_discard_node_capabilities
from .connive_capability_shapes import fixed_connive_node_capabilities
from .goad_capability_shapes import fixed_goad_node_capabilities
from .exploit_capability_shapes import exploit_instruction_node_capabilities
from .source_keyword_choice_shapes import source_keyword_choice_capabilities
from .source_protection_choice_shapes import source_protection_choice_capabilities
from .source_fixed_protection_shapes import source_fixed_protection_capabilities
from .attachment_action_capability_shapes import fixed_attachment_action_node_capabilities
from .counter_capability_shapes import fixed_counter_placement_group_node_capabilities
from .creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities
from .delayed_draw_capability_shapes import fixed_next_turn_draw_node_capabilities
from .graveyard_card_targets import targeted_own_graveyard_return_node_capabilities
from .public_zone_move_capability_shapes import fixed_owner_zone_move_node_capabilities, fixed_public_zone_move_set_node_capabilities, public_graveyard_card_exile_node_capabilities
from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities
from .counter_removal_capabilities import all_counter_removal_node_capabilities, fixed_counter_removal_node_capabilities
from .affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
from .affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities, fixed_controller_discard_node_capabilities, fixed_context_player_discard_node_capabilities
from .entry_return_capability_shapes import fixed_entry_return_node_capabilities
from .node_capability_shapes import fixed_counter_placement_batch_node_capabilities, fixed_counter_placement_node_capabilities, fixed_counter_placement_set_node_capabilities, fixed_counter_placement_target_set_node_capabilities, fixed_target_effect_sequence_node_capabilities, fixed_source_effect_sequence_node_capabilities, fixed_source_characteristics_node_capabilities, fixed_target_characteristics_node_capabilities, temporary_declaration_restriction_node_capabilities, fixed_player_counter_placement_node_capabilities, fixed_damage_node_capabilities, mass_destruction_node_capabilities, fixed_draw_node_capabilities, fixed_scry_node_capabilities, single_explore_node_capabilities, single_proliferate_node_capabilities, self_regeneration_node_capabilities, fixed_self_counter_keyword_action_node_capabilities, fixed_bolster_node_capabilities, fixed_amass_node_capabilities, targeted_counter_node_capabilities, targeted_destruction_node_capabilities, targeted_exile_node_capabilities, targeted_return_to_hand_node_capabilities, targeted_tap_state_node_capabilities
from .token_creation_capability_shapes import fixed_token_creation_node_capabilities
from .event_player_result_shapes import fixed_mill_node_capabilities, fixed_event_player_result_node_capabilities
from .tap_state_event_capability_shapes import tap_state_event_player_node_capabilities
from .scheduled_player_shapes import scheduled_player_node_capabilities
from .impulse_access_capability_shapes import fixed_impulse_access_node_capabilities
from .monarch_capability_shapes import fixed_monarch_node_capabilities
from .transform_capability_shapes import source_transform_node_capabilities
from .class_level_capability_shapes import class_level_node_capabilities
from .library_search_capability_shapes import fixed_library_search_node_capabilities, fixed_type_to_hand_search_node_capabilities, partner_with_search_node_capabilities
from .library_selection_capability_shapes import fixed_library_selection_node_capabilities
from .hand_inspection_capability_shapes import fixed_hand_inspection_node_capabilities
from .temporary_target_interaction_shapes import temporary_target_interaction_node_capabilities
from .surveil_capability_shapes import fixed_surveil_node_capabilities
from .self_return_capability_shapes import fixed_self_return_node_capabilities
from .fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities
from .optional_counter_capability_shapes import optional_fixed_counter_event_trigger_node_capabilities
from .graft_capability_shapes import graft_node_capabilities
from .soulbond_capability_shapes import soulbond_node_capabilities
from .enlist_shapes import enlist_reflexive_capabilities
from .fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities
from .created_token_counter_shapes import created_token_counter_node_capabilities
from .fixed_controller_effect_shapes import fixed_counter_controller_effect_sequence_node_capabilities, fixed_controller_effect_sequence_node_capabilities, fixed_life_node_capabilities
from .fixed_effect_clause_shapes import fixed_effect_clause_sequence_node_capabilities, fixed_optional_mana_payment_node_capabilities, fixed_optional_effect_node_capabilities
from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities, announced_target_set_node_capabilities
from .closed_effect_program_shapes import closed_effect_program_node_capabilities


def effect_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> set[str]:
    from .stack_spell_copy_shapes import fixed_stack_spell_copy_node_capabilities
    from .temporary_cast_restrictions import temporary_cast_node_capabilities
    from .library_search_rule_shapes import temporary_library_search_node_capabilities
    from .random_graveyard_returns import random_graveyard_return_node_capabilities
    from .occurrence_object_results import occurrence_object_node_capabilities
    from .prevention_component_shapes import fixed_prevention_component_capabilities
    from ..attachment_references import AttachmentReferenceError,attachment_information_capabilities
    try:
        dependencies: set[str] = set(attachment_information_capabilities(effects))
    except AttachmentReferenceError:
        return set()
    for resolver in (
        fixed_prevention_component_capabilities,
        created_token_counter_node_capabilities,
        temporary_library_search_node_capabilities,
        occurrence_object_node_capabilities,
        temporary_cast_node_capabilities,
        random_graveyard_return_node_capabilities,
        fixed_stack_spell_copy_node_capabilities,
        fixed_extra_turn_node_capabilities,
        ascend_spell_node_capabilities,
        public_event_counter_set_node_capabilities,
        unchosen_hand_discard_node_capabilities,
        fixed_connive_node_capabilities, fixed_goad_node_capabilities, exploit_instruction_node_capabilities,
        fixed_control_node_capabilities,
        fixed_attachment_action_node_capabilities,
        all_counter_removal_node_capabilities,
        fixed_counter_placement_batch_node_capabilities,
        fixed_counter_placement_group_node_capabilities,
        fixed_counter_placement_node_capabilities,
        fixed_counter_removal_node_capabilities,
        fixed_counter_placement_set_node_capabilities,
        fixed_counter_placement_target_set_node_capabilities,
        fixed_homogeneous_target_set_node_capabilities,
        announced_target_set_node_capabilities,
        fixed_player_counter_placement_node_capabilities,
        optional_fixed_counter_event_trigger_node_capabilities,
        fixed_target_characteristics_node_capabilities, temporary_target_interaction_node_capabilities,
        fixed_source_characteristics_node_capabilities, source_keyword_choice_capabilities,
        source_protection_choice_capabilities,
        source_fixed_protection_capabilities,
        temporary_declaration_restriction_node_capabilities,
        fixed_target_effect_sequence_node_capabilities,
        fixed_source_effect_sequence_node_capabilities,
        fixed_creature_power_damage_node_capabilities,
        fixed_damage_node_capabilities,
        mass_destruction_node_capabilities,
        fixed_next_turn_draw_node_capabilities,
        fixed_draw_node_capabilities,
        fixed_affected_player_discard_node_capabilities,
        fixed_controller_discard_node_capabilities,
        fixed_context_player_discard_node_capabilities,
        fixed_affected_player_sacrifice_node_capabilities,
        fixed_entry_return_node_capabilities,
        fixed_mill_node_capabilities, tap_state_event_player_node_capabilities, scheduled_player_node_capabilities,fixed_event_player_result_node_capabilities,
        fixed_impulse_access_node_capabilities,
        fixed_monarch_node_capabilities,
        source_transform_node_capabilities,
        class_level_node_capabilities,
        fixed_library_search_node_capabilities,
        fixed_type_to_hand_search_node_capabilities, partner_with_search_node_capabilities,
        fixed_library_selection_node_capabilities, fixed_hand_inspection_node_capabilities,
        fixed_life_node_capabilities,
        fixed_controller_effect_sequence_node_capabilities,
        fixed_counter_controller_effect_sequence_node_capabilities,
        fixed_effect_clause_sequence_node_capabilities,
        fixed_optional_mana_payment_node_capabilities,
        fixed_optional_effect_node_capabilities,
        closed_effect_program_node_capabilities,
        fixed_scry_node_capabilities,
        fixed_surveil_node_capabilities,
        fixed_self_return_node_capabilities,
        fixed_resolution_characteristic_set_node_capabilities,
        single_explore_node_capabilities,
        single_proliferate_node_capabilities,
        self_regeneration_node_capabilities,
        fixed_self_counter_keyword_action_node_capabilities,
        graft_node_capabilities,
        soulbond_node_capabilities,
        enlist_reflexive_capabilities,
        fixed_resolution_mana_capabilities,
        fixed_bolster_node_capabilities,
        fixed_amass_node_capabilities,
        targeted_counter_node_capabilities,
        targeted_destruction_node_capabilities,
        targeted_exile_node_capabilities,
        targeted_return_to_hand_node_capabilities,
        targeted_own_graveyard_return_node_capabilities, fixed_target_reanimation_node_capabilities,
        public_graveyard_card_exile_node_capabilities,
        fixed_owner_zone_move_node_capabilities,
        fixed_public_zone_move_set_node_capabilities,
        targeted_tap_state_node_capabilities,
        fixed_token_creation_node_capabilities,
    ):
        dependencies.update(
            resolver(
                effects=effects,
                target_schema=target_schema,
                mechanic_ids=mechanics,
            )
        )
    return dependencies
