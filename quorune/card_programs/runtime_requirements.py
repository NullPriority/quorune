from __future__ import annotations
from ..rules.activation.restriction_requirements import activation_restriction_runtime_dependencies
from typing import Any, Mapping

"""Descriptor-owned runtime requirements beyond registered operation minima."""

from ..object_predicate import object_query_capabilities
from ..next_turn_effect import next_turn_effect_dependencies
from ..crime_model import CRIME_EVENT,CRIME_CAPABILITY,crime_fact_dependencies
from ..adventure import ADVENTURE_CAPABILITY,ADVENTURE_COVERAGE
from ..cast_face_offers import OMEN_COVERAGE
from ..omen import OMEN_CAPABILITY
from ..rules.casting_additional_cost_groups import announced_additional_cost_dependencies
from ..rules.fixed_homogeneous_target_set_capability_shapes import announced_target_runtime_dependencies
from ..rules.resolution_condition_shapes import bound_resolution_condition_runtime_dependencies
from ..rules.target_hand_count_shapes import target_hand_count_runtime_dependencies
from ..rules.temporary_keyword_removal import temporary_keyword_removal_runtime_dependencies
from ..rules.increment_condition import increment_runtime_dependencies
from ..targeted_spell_costs import self_target_reduction_runtime_dependencies
from ..rules.target_characteristic_sets import characteristic_set_runtime_dependencies
from ..multikicker_result_amounts import multikicker_amount_runtime_dependencies
from ..mana_spent_amounts import mana_color_amount_runtime_dependencies
from ..rules.selected_counter_costs import selected_counter_runtime_dependencies
from ..rules.activation.source_counter_choices import any_source_counter_runtime_dependencies
from ..activation_mana_cost import activation_mana_option_runtime_dependencies
from ..semantic_runtime.activated_abilities import activated_catalog_runtime_dependencies
from ..rules.source_keyword_choice_shapes import source_keyword_choice_runtime_dependencies
from ..rules.source_protection_choice_shapes import source_protection_choice_runtime_dependencies
from ..rules.source_fixed_protection_shapes import source_fixed_protection_runtime_dependencies
from ..rules.self_cost_turn_counts import self_cost_turn_count_dependencies
from ..rules.self_cost_public_counters import self_cost_public_counter_dependencies
from ..rules.self_cost_numeric_counts import self_cost_numeric_count_dependencies
from ..rules.public_value_costs import public_value_cost_dependencies
from ..rules.reconfigure import reconfigure_runtime_dependencies
from ..rules.paid_color_entry import paid_color_entry_runtime_dependencies
from ..rules.paid_mana_resolution import paid_mana_resolution_dependencies
from ..rules.provoke_shapes import provoke_program_runtime_dependencies
from ..qualified_hexproof import qualified_hexproof_runtime_dependencies
from ..protection_attachment_exceptions import attached_protection_runtime_dependencies
from ..query_effect_amount_model import cast_x_runtime_dependencies
from ..rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies
from ..rules.linked_exile_return_shapes import linked_ability_exile_runtime_dependencies
from ..rules.resolution_characteristic_shapes import fixed_target_color_runtime_dependencies, temporary_creature_type_runtime_dependencies
from ..rules.closed_effect_program_shapes import closed_effect_program_runtime_dependencies
from ..rules.hand_inspection_capability_shapes import selected_hand_reveal_discard_runtime_dependencies
from ..rules.paradigm import paradigm_runtime_dependencies
from ..rules.token_copy_recipients import target_copy_recipient_runtime_dependencies
from ..rules.spell_self_exile import (
    conditional_spell_return_runtime_dependencies,
    spell_self_bottom_runtime_dependencies, spell_self_exile_runtime_dependencies, spell_self_shuffle_runtime_dependencies,
)
from ..rules.random_graveyard_returns import random_graveyard_return_runtime_dependencies
from ..rules.temporary_cast_restrictions import temporary_cast_runtime_dependencies
from ..rules.keyword_runtime_requirements import keyword_runtime_dependencies
from ..rules.event_subscriptions import (
    FixedEventSubscriptionSet, LAND_PLAY_EVENT, controller_keyword_absence_runtime_dependencies,
    death_keyword_absence_runtime_dependencies, dungeon_completion_dependencies,
    face_transition_event_dependencies, upkeep_hand_runtime_dependencies,
)
from ..rules.fixed_blocker_restriction_shapes import fixed_blocker_runtime_dependencies
from ..rules.source_block_pairs import source_block_pair_runtime_dependencies
from ..rules.nonmana_buyback_costs import nonmana_buyback_runtime_dependencies
from ..rules.token_prowess_requirements import token_prowess_runtime_dependencies
from ..emblem_program_model import compiled_emblem_runtime_dependencies
from ..rules.city_blessing_shapes import city_activation_runtime_dependencies
from ..rules.graveyard_cast_costs import paid_graveyard_runtime_dependencies


from .descriptor_requirements import typed_result_amount_runtime_dependencies


def nonmana_equip_runtime_dependencies(program):
    from ..semantic_runtime.activated_abilities import (
        activated_abilities_from_descriptors, is_structural_activated_ability_catalog_program,
    )
    if program.event != 'activate' or is_structural_activated_ability_catalog_program(program):return ()
    from ..rules.attachment_actions import FIXED_NONMANA_EQUIP_CAPABILITY, FIXED_RESTRICTED_EQUIP_CAPABILITY
    required = set()
    for ability in activated_abilities_from_descriptors(program.handlers):
        if ability.builtin_semantic_key != 'builtin:equip':continue
        required.update(('attachment.action.fixed_source', 'target.revalidate_resolution'))
        if (
            ability.life_payment or ability.energy_payment or ability.choices
            or ability.source_counter_removal_cost is not None
        ):
            required.add(FIXED_NONMANA_EQUIP_CAPABILITY)
        else:
            restricted = (ability.activation_limit is not None or ability.target_schema is not None
                and set(ability.target_schema) - {'zones', 'categories', 'controller', 'creature', 'count'})
            required.add(FIXED_RESTRICTED_EQUIP_CAPABILITY if restricted else 'attachment.equip.fixed_mana')
    return tuple(sorted(required))


def target_death_return_runtime_dependencies(program):
    from ..delayed_object_model import (
        is_target_death_return_effect, target_death_return_node_capabilities, target_death_return_price_is_closed,
    )
    from ..replacement.immutable import FrozenMap
    from ..rules.effect_shape_walkers import nested_effects
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    if not any(is_target_death_return_effect(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = target_death_return_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=set(program.coverage) - {"generated_oracle_ir", "activated_ability"},
    )
    price = program.cost_schema
    if (not required or (program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None or not target_death_return_price_is_closed(price)
            or len(program.handlers) != 1 or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Death return requires its exact battlefield activation and target")
    ability, = activated_abilities_from_descriptors(program.handlers)
    catalog_price = {name: getattr(ability, name) for name in price if name != "text"}
    catalog_price["text"] = ability.cost_text
    if (ability.mana_ability or ability.sorcery_speed or ability.activation_conditions
            or ability.zones != ("battlefield",) or ability.target_schema != FrozenMap(program.target_schema)
            or FrozenMap(catalog_price) != FrozenMap(price)
            or any((ability.mana_cost_options, ability.waterbend, ability.boast, ability.reconfigure,
                    ability.forecast, ability.crew_threshold, ability.saddle_threshold, ability.power_up,
                    ability.random_discard_payment, ability.mill_library_cost, ability.activation_limit,
                    ability.granting_attachment_payment, ability.ability_local_mana_reduction,
                    ability.generic_reduction_per_legendary_creature, ability.source_counter_placement_cost,
                    ability.variable_colored_reveal_price, ability.announced_x_symbols))):
        raise ValueError("Death return target or mana price differs from its activation catalog")
    return required


def is_closed_target_death_return_program(program) -> bool:
    from ..delayed_object_model import TARGET_DEATH_RETURN_CAPABILITY

    if TARGET_DEATH_RETURN_CAPABILITY not in program.capability_dependencies:
        return False
    try:
        required = target_death_return_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _cost_metric_runtime_requirements(program):
    required, blockers = set(), set()
    from ..semantic_runtime.mana_abilities import exert_mana_runtime_dependencies, fixed_mana_life_runtime_dependencies

    for resolver, label in (
        (activated_catalog_runtime_dependencies, "runtime_ability_local_mana_reduction"),
        (fixed_mana_life_runtime_dependencies, "runtime_fixed_mana_life"),
        (exert_mana_runtime_dependencies, "runtime_exert_mana"),
    ):
        try:
            required.update(resolver(program))
        except (TypeError, ValueError, KeyError) as exc:
            blockers.add(f"{label}:invalid:{exc}")
    for resolver, label in (
        (public_value_cost_dependencies, 'runtime_public_value_cost'),
        (self_cost_numeric_count_dependencies, 'runtime_self_cost_numeric_count'),
        (self_cost_public_counter_dependencies, 'runtime_self_cost_public_counters'),
        (self_cost_turn_count_dependencies, 'runtime_self_cost_turn_count'),
    ):
        try:
            required.update(resolver(program.handlers))
        except (TypeError, ValueError) as exc:
            blockers.add(f'{label}:invalid:{exc}')
    from ..semantic_runtime.cast_costs import spell_mana_floor_program_dependencies
    try:
        required.update(spell_mana_floor_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_spell_mana_floor:invalid:{exc}")
    from ..rules.activation.granting_attachment import granting_attachment_runtime_dependencies
    try:
        required.update(granting_attachment_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_granting_attachment_payment:invalid:{exc}')
    from ..rules.paid_revealed_creature_results import paid_revealed_creature_runtime_dependencies
    try:
        required.update(paid_revealed_creature_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_paid_revealed_creature:invalid:{exc}")
    return required, blockers


def _event_subscription_runtime_requirements(program):
    """Validate normalized subscriptions and their shared intervening condition."""
    from ..rules.transform_capability_shapes import PERMANENT_TRANSFORM_CAPABILITY_ID
    from ..transform_timestamp_model import TRANSFORM_EVENT
    from ..mutate_model import MUTATE_EVENT,MUTATE_CAPABILITY
    required,blockers=set(),set()
    from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
    from ..counter_placement_event_model import counter_placement_event_dependencies
    from ..player_result_events import player_result_runtime_dependencies
    from ..rules.spell_cast_events import spell_cast_event_runtime_dependencies
    from ..rules.event_subscriptions import (
        entrant_land_advantage_runtime_dependencies, graveyard_creature_transition_runtime_dependencies,
        physical_card_play_runtime_dependencies,
    )
    from ..rules.attached_greatest_power_condition import attached_greatest_power_runtime_dependencies
    from ..rules.attached_temporary_control import attached_temporary_control_runtime_dependencies
    from ..rules.bounded_hand_cast import (
        damaged_player_hand_cast_runtime_dependencies, legendary_wizard_hand_cast_runtime_dependencies,
    )
    from ..rules.source_upkeep_handoff import source_control_or_conversion_runtime_dependencies
    from ..rules.entrant_name_absence import entrant_name_absence_runtime_dependencies
    from ..rules.monarch_transition import monarch_became_runtime_dependencies
    from ..combat_entry_activations import ninjutsu_activation_trigger_dependencies
    from ..rules.creature_ability_activation import creature_ability_activation_runtime_dependencies
    from ..rules.opponent_library_exile_triggers import library_exile_trigger_runtime_dependencies
    from ..rules.opposing_owner_entry_life import opposing_owner_entry_runtime_dependencies
    from ..rules.draw_step_drawing_settlement import draw_step_settlement_program_dependencies
    from ..rules.trigger_source_information import tapped_draw_damage_runtime_dependencies
    from ..rules.subtype_damage_controller import subtype_damage_program_dependencies
    try:
        required.update(draw_step_settlement_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_draw_step_settlement:invalid:{exc}')
    from ..rules.controlled_spell_counter import controlled_spell_counter_runtime_dependencies
    try:
        subscriptions=FixedEventSubscriptionSet.from_condition(program.event_condition)
        required.update(counter_placement_event_dependencies(program, subscriptions))
        required.update(player_result_runtime_dependencies(program, subscriptions))
        required.update(spell_cast_event_runtime_dependencies(program, subscriptions))
        required.update(physical_card_play_runtime_dependencies(program))
        required.update(graveyard_creature_transition_runtime_dependencies(program, subscriptions))
        required.update(entrant_land_advantage_runtime_dependencies(program))
        required.update(attached_greatest_power_runtime_dependencies(program))
        required.update(attached_temporary_control_runtime_dependencies(program))
        required.update(legendary_wizard_hand_cast_runtime_dependencies(program))
        required.update(source_control_or_conversion_runtime_dependencies(program))
        required.update(damaged_player_hand_cast_runtime_dependencies(program))
        required.update(entrant_name_absence_runtime_dependencies(program))
        required.update(monarch_became_runtime_dependencies(program))
        required.update(ninjutsu_activation_trigger_dependencies(program, subscriptions))
        required.update(creature_ability_activation_runtime_dependencies(program, subscriptions))
        required.update(library_exile_trigger_runtime_dependencies(program))
        required.update(opposing_owner_entry_runtime_dependencies(program))
        required.update(tapped_draw_damage_runtime_dependencies(program))
        required.update(subtype_damage_program_dependencies(program))
        required.update(controlled_spell_counter_runtime_dependencies(program, subscriptions))
        if program.event == TRANSFORM_EVENT or (
            subscriptions is not None and subscriptions.subscription_for(TRANSFORM_EVENT) is not None
        ):
            required.add(PERMANENT_TRANSFORM_CAPABILITY_ID)
        if program.event == LAND_PLAY_EVENT or (
            subscriptions is not None and subscriptions.subscription_for(LAND_PLAY_EVENT) is not None
        ):
            required.add('trigger.event.normalized_public_action')
        if subscriptions is not None:
            required.add('trigger.subscription.fixed_multi_event')
            shared = subscriptions.shared_public_state_condition()
            coverage = set(program.coverage)
            if shared is not None or FIXED_PUBLIC_STATE_INTERVENING_COVERAGE in coverage:
                if shared is None or not {
                    'intervening_condition', FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
                    'fixed_multi_event_subscription',
                } <= coverage:
                    raise ValueError('Conditioned subscriptions require complete intervening coverage')
                required.add('trigger.condition.fixed_public_state')
            if any(child.event.removesuffix('.self') == 'permanent.enter' for child in subscriptions.subscriptions):
                required.add('trigger.event.normalized_zone_change')
            if any(child.event.removesuffix('.self')==MUTATE_EVENT for child in subscriptions.subscriptions):
                required.add(MUTATE_CAPABILITY)
        required.update(upkeep_hand_runtime_dependencies(program), dungeon_completion_dependencies(program, subscriptions))
        required.update(controller_keyword_absence_runtime_dependencies(program))
        required.update(death_keyword_absence_runtime_dependencies(program))
        required.update(face_transition_event_dependencies(program, subscriptions))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_event_subscriptions:invalid:{exc}')
    return required,blockers


def _affected_player_choice_runtime_requirements(program):
    from ..rules.affected_player_sacrifice_capability_shapes import (
        greatest_power_sacrifice_runtime_dependencies,
        most_lands_attack_sacrifice_runtime_dependencies,
        other_player_sacrifice_runtime_dependencies,
        source_excluded_sacrifice_runtime_dependencies,
    )
    from ..rules.event_player_choice_shapes import (
        spell_caster_land_return_runtime_dependencies, upkeep_creature_return_runtime_dependencies,
    )
    from ..rules.target_opponent_counter_choice import target_opponent_counter_choice_runtime_dependencies
    from ..rules.keep_three_sacrifice import keep_three_sacrifice_runtime_dependencies
    from ..rules.ordered_type_sacrifice import ordered_type_sacrifice_program_dependencies
    from ..rules.sacrifice_discard_fallback import sacrifice_discard_fallback_program_dependencies
    from ..rules.announced_x_player_rounds import announced_x_player_rounds_program_dependencies
    from ..rules.optional_source_damage import optional_source_damage_runtime_dependencies
    from ..rules.optional_targeted_control import optional_targeted_control_runtime_dependencies
    from ..rules.opponent_damage_source_sacrifice import opponent_damage_source_sacrifice_runtime_dependencies
    from ..rules.collective_source_damage import collective_source_damage_runtime_dependencies
    from ..rules.cast_trigger_life_payment import cast_trigger_life_payment_runtime_dependencies
    from ..fixed_token_production import announced_populate_runtime_dependencies
    from ..rules.token_creation_capability_shapes import source_bound_populate_runtime_dependencies
    from ..token_copy_recipes import optional_controlled_token_copy_runtime_dependencies
    from ..rules.protected_attack_payments import protected_attack_payment_runtime_dependencies
    from ..rules.opponent_discard_results import opponent_discard_runtime_dependencies
    from ..rules.target_graveyard_escape import target_graveyard_escape_runtime_dependencies
    from ..zone_devour import devour_entry_result_runtime_dependencies
    from ..semantic_runtime.self_entry_counters import entry_counter_transfer_runtime_dependencies

    required, blockers = set(), set()
    for resolver, label in (
        (source_excluded_sacrifice_runtime_dependencies, "source_exclusion"),
        (other_player_sacrifice_runtime_dependencies, "other_player_sacrifice"),
        (most_lands_attack_sacrifice_runtime_dependencies, "most_lands_attack_sacrifice"),
        (greatest_power_sacrifice_runtime_dependencies, "greatest_power_sacrifice"),
        (spell_caster_land_return_runtime_dependencies, "spell_caster_land_return"),
        (upkeep_creature_return_runtime_dependencies, "upkeep_creature_return"),
        (target_opponent_counter_choice_runtime_dependencies, "target_opponent_counter_choice"),
        (keep_three_sacrifice_runtime_dependencies, "keep_three_sacrifice"),
        (ordered_type_sacrifice_program_dependencies, "ordered_type_sacrifice"),
        (sacrifice_discard_fallback_program_dependencies, "sacrifice_discard_fallback"),
        (announced_x_player_rounds_program_dependencies, "announced_x_player_rounds"),
        (optional_source_damage_runtime_dependencies, "optional_source_damage"),
        (optional_targeted_control_runtime_dependencies, "optional_targeted_control"),
        (opponent_damage_source_sacrifice_runtime_dependencies, "opponent_damage_source_sacrifice"),
        (collective_source_damage_runtime_dependencies, "collective_source_damage"),
        (cast_trigger_life_payment_runtime_dependencies, "cast_trigger_life_payment"),
        (announced_populate_runtime_dependencies, "announced_populate"),
        (source_bound_populate_runtime_dependencies, "source_bound_populate"),
        (optional_controlled_token_copy_runtime_dependencies, "optional_controlled_token_copy"),
        (protected_attack_payment_runtime_dependencies, "protected_attack_payment"),
        (opponent_discard_runtime_dependencies, "opponent_effect_discard"),
        (target_graveyard_escape_runtime_dependencies, "target_graveyard_escape"),
        (devour_entry_result_runtime_dependencies, "devour_entry_result"),
        (entry_counter_transfer_runtime_dependencies, "entry_counter_transfer"),
    ):
        try:
            required.update(resolver(program))
        except (ValueError, TypeError, KeyError) as exc:
            blockers.add(f"runtime_{label}:invalid:{exc}")
    return required, blockers


def _target_shape_runtime_requirements(program, required, blockers):
    from ..rules.linked_graveyard_aura import linked_graveyard_aura_program_dependencies

    try:
        required.update(linked_graveyard_aura_program_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_linked_graveyard_aura:invalid:{exc}")
    from ..rules.artifact_sacrifice_return import artifact_sacrifice_return_runtime_dependencies

    try:
        required.update(artifact_sacrifice_return_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_artifact_sacrifice_return:invalid:{exc}")
    from ..rules.fame_fortune_choices import fame_fortune_runtime_dependencies

    try:
        required.update(fame_fortune_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_fame_fortune:invalid:{exc}")
    from ..rules.announced_destruction_controller_tokens import announced_destruction_controller_token_program_dependencies
    from ..rules.artifact_destruction_treasure import artifact_destruction_treasure_program_dependencies
    from ..rules.nonbasic_land_source_untap import nonbasic_land_source_untap_program_dependencies
    from ..rules.destroyed_creature_copy import destroyed_creature_copy_program_dependencies

    try:
        required.update(destroyed_creature_copy_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_destroyed_creature_copy:invalid:{exc}")
    try:
        required.update(nonbasic_land_source_untap_program_dependencies(program))
    except (KeyError, TypeError, ValueError) as exc:
        blockers.add(f"runtime_nonbasic_target_source_untap:invalid:{exc}")
    try:
        required.update(announced_destruction_controller_token_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_announced_destruction_controller_tokens:invalid:{exc}")
    try:
        required.update(artifact_destruction_treasure_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_target_artifact_destruction_treasure:invalid:{exc}")
    from ..rules.target_controller_library_reveal import target_controller_library_program_dependencies

    try:
        required.update(target_controller_library_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_target_controller_library_reveal:invalid:{exc}")
    from ..rules.spell_owner_targets import spell_owner_target_dependencies
    from ..rules.aura_attachment_targets import aura_attachment_destruction_dependencies
    from ..rules.compound_target_sets import optional_destroy_roles_runtime_dependencies
    try:required.update(optional_destroy_roles_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_optional_destroy_roles:invalid:{exc}')
    from ..rules.permanent_predicate_capability_shapes import controller_island_target_runtime_dependencies
    try:required.update(controller_island_target_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:blockers.append(f"runtime_controller_island_target:invalid:{exc}")
    from ..rules.permanent_predicate_capability_shapes import nonartifact_nonblack_target_runtime_dependencies
    try:required.update(nonartifact_nonblack_target_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_qualified_creature_target:invalid:{exc}')
    try:required.update(spell_owner_target_dependencies(getattr(program,'target_schema',None)))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_spell_owner_target:invalid:{exc}')
    try:required.update(aura_attachment_destruction_dependencies(program.effects, program.target_schema))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_aura_attachment_target:invalid:{exc}')


def _scheduled_and_day_night_runtime_dependencies(program):
    from ..day_night_model import day_night_toggle_runtime_dependencies
    from ..rules.scheduled_player_shapes import scheduled_player_departure_runtime_dependencies

    return (*scheduled_player_departure_runtime_dependencies(program),
            *day_night_toggle_runtime_dependencies(program))


def _normalized_event_condition_requirements(program, required, blockers):
    """Bind immediate event facts and subscriptions before their result queries."""
    from ..rules.chosen_color_events import chosen_color_cast_dependencies
    required.update(chosen_color_cast_dependencies(program))
    from ..rules.chosen_type_events import chosen_type_event_dependencies
    required.update(chosen_type_event_dependencies(program))
    from ..permanent_event_state_model import state_qualified_zone_runtime_dependencies
    try:required.update(state_qualified_zone_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_permanent_state:invalid:{exc}')
    from ..permanent_event_state_model import state_qualified_damage_runtime_dependencies
    try:required.update(state_qualified_damage_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_damage_state:invalid:{exc}')
    from ..explore_action_model import CREATURE_EXPLORE_EVENT, CREATURE_EXPLORE_CAPABILITY
    if program.event == CREATURE_EXPLORE_EVENT:required.add(CREATURE_EXPLORE_CAPABILITY)
    from ..mutate_model import MUTATE_EVENT,MUTATE_CAPABILITY
    if program.event.removesuffix('.self')==MUTATE_EVENT:required.add(MUTATE_CAPABILITY)
    if program.event==CRIME_EVENT:required.add(CRIME_CAPABILITY)
    required.update(crime_fact_dependencies((program.handlers,program.event_condition,program.effects,program.cost_schema)))
    subscriptions,subscription_blockers=_event_subscription_runtime_requirements(program)
    required.update(subscriptions);blockers.update(subscription_blockers)


def _event_query_runtime_requirements(program):
    """Infer descriptor-owned subscriptions and normalized event queries."""
    from ..public_history_query_model import public_history_query_dependencies
    from ..spell_history_ordinal_model import qualified_spell_ordinal_dependencies
    from ..rules.qualified_attacks import qualified_attack_runtime_dependencies
    from ..saddle import saddle_activation_runtime_dependencies, saddled_attack_runtime_dependencies
    from ..rules.entry_permanent_results import entry_animation_runtime_dependencies
    from ..rules.transform_capability_shapes import triggered_source_transform_runtime_dependencies
    from ..rules.subtype_transform import subtype_transform_runtime_dependencies
    from ..rules.unblocked_attackers import unblocked_attacker_runtime_dependencies
    from ..rules.block_characteristics import qualified_block_runtime_dependencies
    from ..rules.attack_batches import qualified_attack_batch_runtime_dependencies
    from ..rules.attacked_player_life import attacked_player_life_runtime_dependencies
    from ..rules.combat_damage_entry_history import combat_damage_entry_runtime_dependencies
    from ..rules.event_player_result_shapes import event_player_damage_runtime_dependencies, spell_cast_count_life_runtime_dependencies
    from ..rules.scheduled_turn_skip_shapes import scheduled_turn_skip_event_dependencies
    from ..rules.self_zone_move_followups import self_entry_followup_runtime_dependencies
    from ..rules.source_self_entry_counters import source_self_entry_counter_runtime_dependencies
    from ..rules.combat_defending_players import combat_defending_player_runtime_dependencies
    from ..rules.discard_result_shapes import event_player_discard_result_runtime_dependencies
    from ..rules.event_player_targets import event_player_target_runtime_dependencies
    from ..rules.per_player_target_sets import per_player_target_runtime_dependencies
    from ..rules.reanimation_entry_counters import reanimation_entry_counter_runtime_dependencies
    from ..rules.hand_entry_followup import hand_entry_followup_runtime_dependencies
    from ..rules.finality_counters import finality_counter_runtime_dependencies
    from ..rules.target_set_result_sequences import target_set_result_sequence_runtime_dependencies
    from ..rules.targeted_graveyard_outcomes import targeted_graveyard_outcome_runtime_dependencies
    from ..rules.graveyard_exile_branches import graveyard_exile_branch_runtime_dependencies
    from ..rules.graveyard_exile_tokens import graveyard_exile_token_runtime_dependencies
    from ..rules.graveyard_exile_type_outcomes import graveyard_exile_type_outcome_runtime_dependencies
    from ..rules.target_player_sequences import target_player_sequence_runtime_dependencies
    from ..rules.stack_controller_payment_shapes import stack_controller_payment_scope_dependencies
    from ..rules.fixed_effect_payment_shapes import fixed_payment_event_player_runtime_dependencies
    from ..rules.artifact_search_exchange import artifact_search_exchange_program_dependencies
    from ..rules.reflexive_payment_shapes import reflexive_payment_runtime_dependencies
    from ..rules.token_creation_reflexive import token_creation_reflexive_runtime_dependencies
    from ..cast_event_amounts import cast_mana_value_runtime_dependencies
    from ..rules.event_spell_copy import cast_copy_runtime_dependencies
    from ..rules.gravestorm import gravestorm_program_dependencies
    from ..rules.demonstrate import demonstrate_program_dependencies
    from ..rules.ripple import ripple_program_dependencies
    from ..rules.casting.spell_mana_payments import expend_runtime_dependencies
    from ..rules.modal_node_capabilities import modal_event_runtime_dependencies
    from ..rules.fixed_resolution_mana_shapes import fixed_resolution_mana_runtime_dependencies
    from ..rules.special_node_shapes import special_result_trigger_runtime_dependencies
    from ..rules.state_triggers import state_trigger_runtime_dependencies
    from ..rules.received_damage_events import received_damage_runtime_dependencies
    from ..rules.designation_events import designation_event_runtime_dependencies
    from ..rules.room_unlock import room_runtime_dependencies
    from ..source_entry_cast_information import source_entry_cast_program_dependencies
    from ..rules.tribute import tribute_condition_dependencies
    from ..rules.graveyard_return_choices import graveyard_return_choice_runtime_dependencies
    from ..rules.targeted_graveyard_exile import targeted_graveyard_exile_runtime_dependencies
    from ..rules.additional_beginning import additional_beginning_runtime_dependencies
    from ..triggered_mana_model import fixed_triggered_mana_runtime_dependencies
    required, blockers = _affected_player_choice_runtime_requirements(program)
    from ..entry_x_amount_model import entry_x_amount_runtime_dependencies
    try:required.update(entry_x_amount_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_entry_x_amount:invalid:{exc}')
    from ..ravenous_model import ravenous_runtime_dependencies
    try:required.update(ravenous_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_ravenous:invalid:{exc}')
    from ..rules.source_sacrifice_shapes import fixed_source_sacrifice_runtime_dependencies
    try:required.update(fixed_source_sacrifice_runtime_dependencies(program))
    except (ValueError,TypeError,KeyError) as exc:blockers.add(f'runtime_source_sacrifice:invalid:{exc}')
    from ..rules.referenced_damage_shapes import referenced_damage_runtime_dependencies
    try:required.update(referenced_damage_runtime_dependencies(program))
    except (ValueError,TypeError,KeyError) as exc:blockers.add(f'runtime_referenced_damage:invalid:{exc}')
    from ..rules.graveyard_self_return import graveyard_self_return_runtime_dependencies
    try:required.update(graveyard_self_return_runtime_dependencies(program))
    except (ValueError,TypeError,KeyError) as exc:blockers.add(f'runtime_graveyard_source_return:invalid:{exc}')
    _target_shape_runtime_requirements(program, required, blockers)
    _normalized_event_condition_requirements(program, required, blockers)
    for resolver,value,label in (
        (public_history_query_dependencies,program.event_condition,'public_history'),
        (qualified_spell_ordinal_dependencies,program.event_condition,'qualified_spell_ordinal'),
        (qualified_attack_runtime_dependencies,program,'qualified_attack'),
        (saddle_activation_runtime_dependencies,program,'saddle_activation'),
        (entry_animation_runtime_dependencies,program,'entry_animation'),
        (triggered_source_transform_runtime_dependencies,program,'triggered_source_transform'),
        (subtype_transform_runtime_dependencies,program,'fixed_subtype_transform'),
        (saddled_attack_runtime_dependencies,program,'saddled_attack'),
        (unblocked_attacker_runtime_dependencies,program,'unblocked_attacker'),
        (qualified_block_runtime_dependencies,program,'qualified_block'),
        (qualified_attack_batch_runtime_dependencies,program,'qualified_attack_batch'),
        (attacked_player_life_runtime_dependencies,program,'attacked_player_life'),
        (combat_damage_entry_runtime_dependencies,program,'combat_damage_entry_history'),
        (event_player_damage_runtime_dependencies,program,'event_player_damage'),
        (spell_cast_count_life_runtime_dependencies,program,'spell_cast_count_life'),
        (scheduled_turn_skip_event_dependencies,program,'scheduled_turn_skip_player'),
        (self_entry_followup_runtime_dependencies,program,'self_entry_followup'),
        (source_self_entry_counter_runtime_dependencies,program,'source_self_entry_counter'),
        (combat_defending_player_runtime_dependencies,program,'combat_defending_player'),
        (event_player_discard_result_runtime_dependencies,program,'event_player_discard_result'),
        (event_player_target_runtime_dependencies,program,'event_player_target'),
        (per_player_target_runtime_dependencies,program,'per_player_targets'),
        (reanimation_entry_counter_runtime_dependencies,program,'reanimation_entry_counters'),
        (hand_entry_followup_runtime_dependencies,program,'hand_entry_followup'),
        (finality_counter_runtime_dependencies,program,'finality_counter'),
        (target_set_result_sequence_runtime_dependencies,program,'target_set_result_sequence'),
        (targeted_graveyard_outcome_runtime_dependencies,program,'targeted_graveyard_outcome'),
        (graveyard_exile_branch_runtime_dependencies,program,'graveyard_exile_branch'),
        (graveyard_exile_token_runtime_dependencies,program,'graveyard_exile_token'),
        (graveyard_exile_type_outcome_runtime_dependencies,program,'graveyard_exile_type_outcomes'),
        (target_player_sequence_runtime_dependencies,program,'target_player_sequence'),
        (stack_controller_payment_scope_dependencies,program,'stack_controller_payment_scope'),
        (fixed_payment_event_player_runtime_dependencies,program,'payment_event_player'),
        (artifact_search_exchange_program_dependencies,program,'artifact_search_exchange'),
        (reflexive_payment_runtime_dependencies,program,'reflexive_payment'),
        (token_creation_reflexive_runtime_dependencies,program,'token_creation_reflexive'),
        (cast_mana_value_runtime_dependencies,program,'cast_spell_mana_value'),
        (cast_copy_runtime_dependencies,program,'event_spell_copy'),
        (gravestorm_program_dependencies,program,'gravestorm'),
        (demonstrate_program_dependencies,program,'demonstrate'),
        (ripple_program_dependencies,program,'ripple'),
        (expend_runtime_dependencies,program,'expend'),
        (modal_event_runtime_dependencies,program,'modal_event'),
        (fixed_resolution_mana_runtime_dependencies,program,'fixed_resolution_mana'),
        (special_result_trigger_runtime_dependencies,program,'special_result_trigger'),
        (state_trigger_runtime_dependencies,program,'state_trigger'),
        (received_damage_runtime_dependencies,program,'received_damage'),
        (designation_event_runtime_dependencies,program,'designation_event'),
        (room_runtime_dependencies,program,'room'),
        (source_entry_cast_program_dependencies,program,'source_entry_cast'),
        (tribute_condition_dependencies,program,'tribute_entry'),
        (graveyard_return_choice_runtime_dependencies,program,'graveyard_return_choice'),
        (targeted_graveyard_exile_runtime_dependencies,program,'targeted_graveyard_exile'),
        (_scheduled_and_day_night_runtime_dependencies,program,'scheduled_player_departure'),
        (additional_beginning_runtime_dependencies,program,'additional_beginning'),
        (fixed_triggered_mana_runtime_dependencies,program,'fixed_triggered_mana'),
    ):
        try:required.update(resolver(value))
        except (ValueError,TypeError) as exc:blockers.add(f'runtime_{label}:invalid:{exc}')
    return required,blockers


def _intrinsic_spell_face_dependencies(coverage):
    return {capability for marker, capability in (
        (ADVENTURE_COVERAGE, ADVENTURE_CAPABILITY), (OMEN_COVERAGE, OMEN_CAPABILITY),
    ) if marker in coverage}


def _validate_paid_sacrifice_mana_value_scope(program, values, amounts):
    from ..compiler.public_query_effect_amounts import public_query_amount_program_is_closed
    from ..scalar_effect_amount_model import (
        PAID_SACRIFICE_MANA_VALUE_CAPABILITY, PAID_SACRIFICE_MANA_VALUE_MECHANIC,
        SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, scalar_amount_specs,
    )

    paid_value = PAID_SACRIFICE_MANA_VALUE_CAPABILITY in amounts
    if paid_value != (PAID_SACRIFICE_MANA_VALUE_MECHANIC in program.coverage):
        raise ValueError('Paid sacrifice mana value requires its typed scalar and mechanic')
    if not paid_value:
        return
    specs = scalar_amount_specs(values)
    if (
        program.event != 'resolve' or not program.ability_id.startswith('spell:')
        or len(specs) != 1 or specs[0].origin is not ScalarAmountOrigin.PAID_SACRIFICE_MANA_VALUE
        or scalar_amount_specs(program.effects) != specs
        or not public_query_amount_program_is_closed(
            program, required_dependencies=(SCALAR_AMOUNT_CAPABILITY, PAID_SACRIFICE_MANA_VALUE_CAPABILITY),
        )
    ):
        raise ValueError('Paid sacrifice mana value requires one closed spell result and its fixed sacrifice')


def _characteristic_quantity_runtime_requirements(program, values):
    from ..characteristic_fragments import characteristic_quantity_capabilities
    from ..rules.live_modifier_quantities import validate_chosen_color_definition_program

    required = set(characteristic_quantity_capabilities((*values, program.event_condition)))
    blockers = set()
    try:
        validate_chosen_color_definition_program(program, required)
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_chosen_color_definition:invalid:{exc}")
    return required, blockers


def _validate_paid_activation_amount(program, amounts, required):
    from ..activation_paid_characteristic_model import (
        ACTIVATION_PAID_STAT_CAPABILITY, ACTIVATION_PAID_STAT_MECHANIC,
    )
    from ..rules.activation.paid_characteristic_results import (
        activation_paid_stat_program_is_closed, source_sacrifice_power_life_runtime_dependencies,
    )
    required.update(source_sacrifice_power_life_runtime_dependencies(program))
    paid_activation = ACTIVATION_PAID_STAT_CAPABILITY in amounts
    if (paid_activation != (ACTIVATION_PAID_STAT_MECHANIC in program.coverage)
            or paid_activation and not activation_paid_stat_program_is_closed(program)):
        raise ValueError('Paid activation stats require their closed activation price and result')


def _value_runtime_requirements(program):
    """Infer the existing minima for typed values and their public predicates."""
    from ..rules.casting.public_conditions import self_public_condition_dependencies
    from ..rules.casting_additional_costs import fixed_creature_sacrifice_price
    from ..scalar_effect_amount_model import PAID_SACRIFICE_STAT_CAPABILITY, PAID_SACRIFICE_STAT_MECHANIC

    values = (program.effects, program.handlers, program.target_schema, program.cost_schema)
    required, blockers = _characteristic_quantity_runtime_requirements(program, values)
    from ..rules.event_optional_draw_shapes import event_optional_draw_runtime_dependencies
    try:required.update(event_optional_draw_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:blockers.add(f'runtime_event_optional_draw:invalid:{exc}')
    from ..rules.damage_capability_shapes import turn_damage_prevention_runtime_dependencies
    from ..rules.fixed_controller_effect_shapes import temporary_life_gain_runtime_dependencies
    from ..rules.public_life_gain_scopes import public_life_gain_runtime_dependencies
    try:
        required.update(public_life_gain_runtime_dependencies(program))
        required.update(temporary_life_gain_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_public_life_gain:invalid:{exc}')
    try:
        required.update(turn_damage_prevention_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_turn_damage_prevention:invalid:{exc}')
    from ..rules.target_library_shuffle import target_shuffle_runtime_dependencies
    try:required.update(target_shuffle_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:blockers.add(f'runtime_target_library_shuffle:invalid:{exc}')
    from ..rules.permanent_subtype_hand_queries import permanent_subtype_hand_runtime_dependencies
    try:required.update(permanent_subtype_hand_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:blockers.add(f'runtime_permanent_subtype_hand:invalid:{exc}')
    from ..source_prevention import source_block_combat_runtime_dependencies
    try:required.update(source_block_combat_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:blockers.add(f'runtime_source_block_combat:invalid:{exc}')
    from ..rules.target_all_counter_removal import target_all_counter_variant_runtime_dependencies
    try:required.update(target_all_counter_variant_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:blockers.add(f'runtime_target_all_counter_variant:invalid:{exc}')
    from ..rules.chosen_counter_removal import chosen_counter_removal_runtime_dependencies
    try:required.update(chosen_counter_removal_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:blockers.add(f'runtime_chosen_counter_removal:invalid:{exc}')
    from ..rules.conditional_target_preparation import conditional_preparation_runtime_dependencies
    try:
        required.update(conditional_preparation_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_conditional_target_preparation:invalid:{exc}')
    from ..reciprocal_power_damage import reciprocal_power_damage_runtime_dependencies
    try:
        required.update(reciprocal_power_damage_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_reciprocal_power_damage:invalid:{exc}')
    from ..rules.stack_counter_followup import stack_counter_followup_runtime_dependencies
    try:
        required.update(stack_counter_followup_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_stack_counter_followup:invalid:{exc}")
    from ..rules.fixed_dual_damage import fixed_dual_damage_runtime_dependencies
    try:
        required.update(fixed_dual_damage_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_fixed_target_controller_damage:invalid:{exc}')
    try:
        required.update(next_turn_effect_dependencies(program.effects))
    except ValueError as exc:
        blockers.add(f"runtime_next_turn_duration:invalid:{exc}")
    from ..source_tap_effect import source_tap_effect_dependencies
    from ..rules.tap_state_capability_shapes import untap_runtime_dependencies
    try:
        required.update(source_tap_effect_dependencies(program.effects))
        required.update(untap_runtime_dependencies(program))
    except ValueError as exc:
        blockers.add(f"runtime_source_tap_duration:invalid:{exc}")
    try:
        required.update(self_public_condition_dependencies((program.handlers, program.cost_schema)))
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_self_public_condition:invalid:{exc}")
    try:
        from ..rules.same_name_zone_moves import same_name_zone_move_runtime_dependencies

        required.update(same_name_zone_move_runtime_dependencies(program))
        amounts = typed_result_amount_runtime_dependencies(values, mechanics=tuple(program.coverage))
        _validate_paid_sacrifice_mana_value_scope(program, values, amounts)
        from ..rules.reflexive_payment_shapes import validate_reflexive_paid_power_scope
        validate_reflexive_paid_power_scope(program.effects, values, amounts)
        from ..announced_damage import VARIABLE_DAMAGE_ALLOCATION_CAPABILITY, variable_damage_allocation
        from ..fractional_quantity_model import HALF_MILL_CAPABILITY
        from ..target_cardinality import SELECTED_PLAYER_RESULT_CAPABILITY
        from ..fixed_top_exile import FIXED_TOP_EXILE_CAPABILITY
        if VARIABLE_DAMAGE_ALLOCATION_CAPABILITY in amounts or variable_damage_allocation(program.target_schema) is not None:
            from ..rules.damage_capability_shapes import variable_damage_allocation_program_requirements

            required.update(variable_damage_allocation_program_requirements(program))
        from ..resolution_characteristic_model import DERIVED_CHARACTERISTIC_CAPABILITY
        if DERIVED_CHARACTERISTIC_CAPABILITY in amounts:
            from ..rules.resolution_characteristic_shapes import validate_derived_characteristic_program

            required.update(validate_derived_characteristic_program(program))
        if FIXED_TOP_EXILE_CAPABILITY in amounts:
            from ..rules.discover_capability_shapes import validate_fixed_top_exile_program_scope
            validate_fixed_top_exile_program_scope(program, values)
        if SELECTED_PLAYER_RESULT_CAPABILITY in amounts:
            from ..rules.any_number_target_shapes import selected_player_result_node_capabilities
            from ..rules.monarch_selected_player_mill import monarch_selected_player_mill_runtime_dependencies

            selected = monarch_selected_player_mill_runtime_dependencies(program) or selected_player_result_node_capabilities(
                effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
            )
            if not selected:
                raise ValueError("Selected player results require their closed any-number target program")
            required.update(selected)
        if HALF_MILL_CAPABILITY in amounts:
            from ..rules.mill_capability_shapes import validate_half_mill_program_scope
            validate_half_mill_program_scope(program)
        from ..query_effect_amount_model import OPPONENT_HAND_MILL_CAPABILITY
        if OPPONENT_HAND_MILL_CAPABILITY in amounts and program.target_schema is not None:
            raise ValueError("Opponent hand-count Mill does not announce targets")
        paid = PAID_SACRIFICE_STAT_CAPABILITY in amounts
        _validate_paid_activation_amount(program, amounts, required)
        if paid != (PAID_SACRIFICE_STAT_MECHANIC in program.coverage) or (paid and (
            program.event != 'resolve' or not program.ability_id.startswith('spell:')
            or not fixed_creature_sacrifice_price(program.cost_schema)
        )):
            raise ValueError('Paid creature stats require their closed spell and fixed creature sacrifice price')
        required.update(amounts)
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_typed_result_amount:invalid:{exc}")
    try:
        from ..rules.fixed_effect_clause_shapes import source_characteristic_tap_runtime_dependencies
        required.update(source_characteristic_tap_runtime_dependencies(program))
        required.update(fixed_resolution_characteristics_runtime_dependencies(program))
        required.update(temporary_creature_type_runtime_dependencies(program))
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_resolution_characteristics:invalid:{exc}")
    try:
        required.update(linked_ability_exile_runtime_dependencies(program))
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_linked_ability_exile:invalid:{exc}")
    try:
        required.update(fixed_target_color_runtime_dependencies(program))
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_fixed_target_color:invalid:{exc}")
    from ..rules.defender_permission_effect import temporary_defender_permission_runtime_dependencies
    try:
        required.update(temporary_defender_permission_runtime_dependencies(program))
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_defender_permission:invalid:{exc}")
    try:
        required.update(cast_x_runtime_dependencies(values))
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_cast_x_amount:invalid:{exc}")
    return required, blockers


def stack_spell_target_counter_runtime_dependencies(program) -> tuple[str, ...]:
    from ..replacement.immutable import thaw_value
    from ..stack_spell_targeting import (
        CURRENT_TARGET_COUNTER, CURRENT_TARGET_COUNTER_CAPABILITIES, StackSpellTargetPredicate,
        contains_stack_spell_target_predicate, stack_spell_target_predicate,
    )

    schema = program.target_schema
    if not contains_stack_spell_target_predicate(schema):
        return ()
    predicate = stack_spell_target_predicate(schema)
    event_zone = (program.event, program.active_zone)
    if predicate is None or event_zone not in {("resolve", "stack"), ("activate", "battlefield")}:
        raise ValueError("A spell-target subject requires a closed counter resolution or activation")
    if predicate is StackSpellTargetPredicate.SOURCE and program.event != "activate":
        raise ValueError("A current-source subject requires a battlefield activation")
    effects = tuple(thaw_value(effect) for effect in program.effects)
    if not effects or effects[0] != CURRENT_TARGET_COUNTER:
        raise ValueError("A spell-target subject requires the ordinary counter as its first result")
    if len(effects) == 1:
        return CURRENT_TARGET_COUNTER_CAPABILITIES
    if (program.event != "resolve" or len(effects) != 2
            or effects[1] != {"op": "draw", "player": "$controller", "count": 1, "private": True}
            or type(effects[1].get("count")) is not int or effects[1].get("private") is not True):
        raise ValueError("A counter with a spell-target subject permits only one private draw afterward")
    from ..compiler.program_composition import composed_spell_family_runtime_dependencies

    draw = composed_spell_family_runtime_dependencies(
        program, capability="zone.draw.library_to_hand", mechanic="cr-121-drawing-a-card",
    )
    if not draw:
        raise ValueError("The counter and private draw require exact printed composition")
    return (*CURRENT_TARGET_COUNTER_CAPABILITIES, *draw)


def _zone_move_runtime_requirements(program, required, blockers):
    from ..rules.linked_library_imprint import library_imprint_program_dependencies
    from ..rules.search_random_discard import private_search_random_discard_runtime_dependencies
    from ..rules.private_opponent_library_cast_shapes import private_opponent_library_cast_program_dependencies
    from ..rules.linked_color_cast_exile import linked_color_cast_exile_dependencies

    try:
        required.update(private_search_random_discard_runtime_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_private_search_random_discard:invalid:{exc}")
    try:
        from ..rules.linked_hand_mana import linked_hand_mana_program_dependencies

        required.update(library_imprint_program_dependencies(program))
        required.update(linked_hand_mana_program_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_linked_library_imprint:invalid:{exc}")
    try:
        required.update(linked_color_cast_exile_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_linked_color_cast_exile:invalid:{exc}")
    try:
        required.update(_source_position_and_graveyard_requirements(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_owner_library_position:invalid:{exc}")
    from ..stack_counter import (
        announced_x_counter_runtime_dependencies, creature_count_counter_runtime_dependencies, no_mana_spent_counter_runtime_dependencies,
        other_controller_ability_counter_runtime_dependencies,
    )
    from ..public_zone_moves import stack_spell_exile_runtime_dependencies
    from ..rules.opponent_spell_or_permanent_return import opponent_spell_or_permanent_return_runtime_dependencies
    from ..rules.milled_creature_tokens import milled_creature_token_program_dependencies
    from ..rules.destruction_regeneration_capability_shapes import kicked_entry_destruction_runtime_dependencies
    from ..rules.opponent_greatest_power_destruction import greatest_power_destruction_runtime_dependencies
    from ..rules.opponent_greatest_power_exile import greatest_power_exile_runtime_dependencies
    from ..rules.greatest_power_target_shapes import greatest_power_target_runtime_dependencies
    from ..rules.chosen_color_target_shapes import chosen_color_target_runtime_dependencies
    from ..rules.chosen_type_target_shapes import chosen_creature_type_target_runtime_dependencies
    from ..rules.temporary_self_copy import temporary_creature_copy_set_runtime_dependencies, temporary_self_copy_runtime_dependencies
    from ..rules.temporary_copy_subject import temporary_copy_subject_runtime_dependencies
    from ..rules.persistent_public_copy import controlled_artifact_copy_runtime_dependencies, persistent_public_copy_runtime_dependencies
    from ..rules.linked_exile_copy import linked_exile_copy_runtime_dependencies
    from ..rules.announced_artifact_copy import announced_artifact_copy_runtime_dependencies
    from ..rules.announced_graveyard_copy import announced_graveyard_copy_runtime_dependencies
    from ..rules.graveyard_exile_copy import graveyard_exile_copy_runtime_dependencies
    from ..rules.temporary_occurrence_copy import temporary_occurrence_copy_runtime_dependencies
    from ..rules.token_creation_capability_shapes import graveyard_target_token_copy_runtime_dependencies
    from ..rules.control_capability_shapes import (
        permanent_control_exchange_runtime_dependencies, two_controller_creature_transfer_runtime_dependencies,
    )
    from ..rules.library_bottom_results import library_bottom_result_runtime_dependencies
    from ..rules.reveal_until_reflexive import reveal_until_reflexive_runtime_dependencies
    from ..rules.recipient_control_shapes import recipient_control_runtime_dependencies
    from ..rules.owner_control_shapes import owner_control_runtime_dependencies
    from ..rules.aggregated_arrival_counters import matching_arrival_counter_program_dependencies
    from ..counter_placement_sets import (
        source_and_controlled_subtype_program_dependencies, source_power_combat_counter_program_dependencies,
    )
    from ..rules.controlled_counter_monstrosity import controlled_counter_monstrosity_runtime_dependencies
    from ..rules.event_counter_threshold import event_counter_threshold_program_dependencies
    from ..rules.graveyard_card_targets import (
        graveyard_copy_cast_runtime_dependencies,
        opposing_graveyard_return_runtime_dependencies, public_graveyard_return_runtime_dependencies,
        targeted_graveyard_cast_runtime_dependencies,
    )
    from ..rules.controlled_return import controlled_return_runtime_dependencies
    from ..rules.commander_return_choice import commander_return_runtime_dependencies
    from ..rules.void_counter_play import runtime_dependencies as void_play_dependencies
    from ..rules.entry_return_capability_shapes import checked_entry_return_runtime_dependencies
    from ..rules.public_zone_move_capability_shapes import (
        chosen_color_permanent_return_runtime_dependencies, chosen_type_tap_state_runtime_dependencies,
        targeted_same_turn_graveyard_return_runtime_dependencies,
        triggered_source_exile_runtime_dependencies,
    )
    from ..rules.reanimation_capability_shapes import (
        attached_event_battlefield_return_runtime_dependencies,
        source_power_reanimation_runtime_dependencies,
    )

    from ..rules.source_bound_delayed_return import source_bound_return_runtime_dependencies
    from ..rules.enchanted_departure_events import enchanted_departure_return_runtime_dependencies
    from ..rules.death_battlefield_return import death_battlefield_return_runtime_dependencies
    from ..rules.discarded_card_exile import discarded_card_exile_runtime_dependencies

    for resolver, label in (
        (discarded_card_exile_runtime_dependencies, "discarded_card_exile"),
        (private_opponent_library_cast_program_dependencies, 'private_opponent_library_cast'),
        (source_bound_return_runtime_dependencies, 'source_bound_delayed_return'),
        (target_death_return_runtime_dependencies, 'target_death_return_to_hand'),
        (death_battlefield_return_runtime_dependencies, 'death_battlefield_return'),
        (source_and_controlled_subtype_program_dependencies, 'source_and_controlled_subtype_counter'),
        (source_power_combat_counter_program_dependencies, 'source_power_combat_counter_set'),
        (controlled_counter_monstrosity_runtime_dependencies, 'controlled_counter_monstrosity'),
        (event_counter_threshold_program_dependencies, 'event_counter_threshold'),
        (matching_arrival_counter_program_dependencies, 'matching_arrival_counter'),
        (other_controller_ability_counter_runtime_dependencies, 'other_controller_ability_counter'),
        (no_mana_spent_counter_runtime_dependencies, 'no_mana_spent_counter'),
        (announced_x_counter_runtime_dependencies, 'announced_x_counter'),
        (creature_count_counter_runtime_dependencies, 'creature_count_counter'),
        (stack_spell_target_counter_runtime_dependencies, 'stack_spell_target_counter'),
        (paradigm_runtime_dependencies, 'paradigm'),
        (target_copy_recipient_runtime_dependencies, 'target_copy_recipient'),
        (spell_self_exile_runtime_dependencies, 'spell_self_exile'),
        (spell_self_shuffle_runtime_dependencies, 'spell_self_shuffle'),
        (spell_self_bottom_runtime_dependencies, 'spell_self_bottom'),
        (conditional_spell_return_runtime_dependencies, 'conditional_spell_return'),
        (selected_hand_reveal_discard_runtime_dependencies, 'selected_hand_reveal_discard'),
        (triggered_source_exile_runtime_dependencies, 'triggered_source_exile'),
        (chosen_color_permanent_return_runtime_dependencies, 'chosen_color_permanent_return'),
        (chosen_type_tap_state_runtime_dependencies, 'chosen_type_tap_state'),
        (targeted_same_turn_graveyard_return_runtime_dependencies, 'targeted_same_turn_graveyard_return'),
        (public_graveyard_return_runtime_dependencies, 'public_graveyard_return'),
        (opposing_graveyard_return_runtime_dependencies, 'opposing_graveyard_return'),
        (targeted_graveyard_cast_runtime_dependencies, 'targeted_graveyard_cast'),
        (graveyard_copy_cast_runtime_dependencies, 'graveyard_copy_cast'),
        (source_power_reanimation_runtime_dependencies, 'source_power_reanimation'),
        (checked_entry_return_runtime_dependencies, 'checked_entry_return'),
        (controlled_return_runtime_dependencies, 'controlled_return'),
        (commander_return_runtime_dependencies, 'commander_return'),
        (void_play_dependencies, 'void_counter_play'),
        (stack_spell_exile_runtime_dependencies, 'stack_spell_exile'),
        (opponent_spell_or_permanent_return_runtime_dependencies, 'opponent_spell_or_permanent_return'),
        (milled_creature_token_program_dependencies, 'milled_creature_tokens'),
        (kicked_entry_destruction_runtime_dependencies, 'kicked_entry_destruction'),
        (greatest_power_destruction_runtime_dependencies, 'opponent_greatest_power_destruction'),
        (greatest_power_exile_runtime_dependencies, 'opponent_greatest_power_exile'),
        (greatest_power_target_runtime_dependencies, 'greatest_power_creature_target'),
        (chosen_creature_type_target_runtime_dependencies, 'source_chosen_creature_type_target'),
        (chosen_color_target_runtime_dependencies, 'source_chosen_color_target'),
        (temporary_self_copy_runtime_dependencies, 'temporary_self_copy'),
        (linked_exile_copy_runtime_dependencies, 'linked_exile_self_copy'),
        (announced_artifact_copy_runtime_dependencies, 'announced_artifact_self_copy'),
        (announced_graveyard_copy_runtime_dependencies, 'announced_graveyard_self_copy'),
        (graveyard_exile_copy_runtime_dependencies, 'graveyard_exile_self_copy'),
        (temporary_creature_copy_set_runtime_dependencies, 'temporary_creature_copy_set'),
        (temporary_copy_subject_runtime_dependencies, 'temporary_copy_subject'),
        (persistent_public_copy_runtime_dependencies, 'persistent_public_copy'),
        (controlled_artifact_copy_runtime_dependencies, 'controlled_artifact_copy'),
        (temporary_occurrence_copy_runtime_dependencies, 'temporary_occurrence_copy'),
        (graveyard_target_token_copy_runtime_dependencies, 'graveyard_target_token_copy'),
        (recipient_control_runtime_dependencies,'recipient_control'),
        (permanent_control_exchange_runtime_dependencies, 'permanent_control_exchange'),
        (two_controller_creature_transfer_runtime_dependencies, 'two_controller_creature_transfer'),
        (library_bottom_result_runtime_dependencies, 'library_bottom_creature_result'),
        (reveal_until_reflexive_runtime_dependencies, 'reveal_until_reflexive'),
        (owner_control_runtime_dependencies,'owner_control'),
        (attached_event_battlefield_return_runtime_dependencies, 'attached_event_battlefield_return'),
        (enchanted_departure_return_runtime_dependencies, 'enchanted_departure_return'),
    ):
        try:
            required.update(resolver(program))
        except (ValueError, TypeError) as exc:
            blockers.add(f'runtime_{label}:invalid:{exc}')


def _attached_subtype_otherwise_runtime_dependencies(program):
    from ..bound_declaration_model import (
        ATTACHED_SUBTYPE_OTHERWISE_TEMPLATE, BOUND_DECLARATION_HANDLER,
        BoundStaticDeclarationRulesSpec, attached_subtype_otherwise_restrictions,
    )

    if program.provenance.get("template_id") != ATTACHED_SUBTYPE_OTHERWISE_TEMPLATE:
        return ()
    from ..semantic_runtime.conditional_continuous import FixedPublicStateCharacteristicsHandler

    if ((program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
            or len(program.handlers) != 2 or program.target_schema is not None or program.cost_schema is not None):
        raise ValueError("Complementary attached branches need both original static components")
    stats, restraint = program.handlers
    node = FixedPublicStateCharacteristicsHandler().validate(stats)
    if (node.target_kind != "attached" or node.subject_types_all != ("creature",)
            or node.abilities or node.fragments or not (node.power or node.toughness)):
        raise ValueError("A positive attached subtype supplies a fixed stat modifier")
    expected = attached_subtype_otherwise_restrictions(node.source_condition)
    fragment = restraint.get("fragment", {})
    if (restraint.get("handler_id") != BOUND_DECLARATION_HANDLER
            or fragment.get("kind") != "bound_static_declaration_rules"
            or BoundStaticDeclarationRulesSpec.from_dict(fragment.get("value")) != expected):
        raise ValueError("The negative branch must prohibit attack and block on the complementary attached creature")
    return tuple(sorted({"continuous.attached.fixed_characteristics", *expected.required_capabilities()}))


def _fixed_keywords_and_color_protection_runtime_dependencies(program):
    from ..compiler.attached_ability_templates import FIXED_KEYWORDS_AND_COLOR_PROTECTION_TEMPLATE

    if program.provenance.get("template_id") != FIXED_KEYWORDS_AND_COLOR_PROTECTION_TEMPLATE:
        return ()
    if ((program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
            or len(program.handlers) != 2 or program.target_schema is not None or program.cost_schema is not None):
        raise ValueError("Fixed keywords and color protection require both original static grants")
    ordinary, protected = program.handlers
    if (ordinary.get("handler_id") != "continuous.ability.fixed-query-keyword-grant.v1"
            or protected.get("handler_id") != "continuous.ability.fixed-query-protection-grant.v2"
            or ordinary.get("condition") != protected.get("condition")):
        raise ValueError("Fixed keywords and color protection must govern the same current recipients")
    return ("continuous.ability.fixed_query_keyword_grant", "protection.typed.debt")


def _turn_source_enchantment_creature_keywords_runtime_dependencies(program):
    from ..compiler.conditional_granted_ability_templates import TURN_SOURCE_ENCHANTMENT_CREATURE_KEYWORDS_TEMPLATE

    if program.provenance.get("template_id") != TURN_SOURCE_ENCHANTMENT_CREATURE_KEYWORDS_TEMPLATE:
        return ()
    from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
    from ..object_predicate import ObjectQuerySpec
    from ..semantic_runtime.conditional_continuous import ConditionalTypedKeywordHandler

    if ((program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
            or len(program.handlers) != 2 or program.target_schema is not None
            or program.cost_schema is not None or program.effects
            or any("recipient" in item for item in program.handlers)):
        raise ValueError("Conditional source and enchantment-creature grants require both original static branches")
    handler = ConditionalTypedKeywordHandler()
    source, others = (handler.validate(item) for item in program.handlers)
    controller_turn = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_TURN)
    if (source.source_condition != controller_turn or others.source_condition != controller_turn
            or source.target_kind != "source" or others.target_kind != "fixed_query"
            or others.target_controller != "source_controller" or not others.exclude_source
            or others.predicate != ObjectQuerySpec(zones=("battlefield",), types_all=("creature", "enchantment"))):
        raise ValueError("The current controller turn grant requires disjoint source and enchantment-creature recipients")
    if (source.power or source.toughness or others.power or others.toughness
            or source.abilities != others.abilities or source.fragments != others.fragments):
        raise ValueError("Conditional source and enchantment creatures require the same complete typed keyword list")
    return handler.capability_dependencies


def _controller_player_and_permanent_hexproof_runtime_dependencies(program):
    from ..compiler.player_target_protection_nodes import (
        PLAYER_HUMAN_HEXPROOF_TEMPLATE, PLAYER_PLANESWALKER_CREATURE_HEXPROOF_TEMPLATE,
    )

    template = program.provenance.get("template_id")
    if template not in {PLAYER_HUMAN_HEXPROOF_TEMPLATE, PLAYER_PLANESWALKER_CREATURE_HEXPROOF_TEMPLATE}:
        return ()
    from ..object_predicate import ObjectQuerySpec
    from ..semantic_runtime.continuous_components import FixedQueryKeywordGrantHandler
    from ..semantic_runtime.player_target_protection import PlayerTargetProtectionHandler

    battlefield = ("battlefield",)
    if template == PLAYER_HUMAN_HEXPROOF_TEMPLATE:
        expected = ((ObjectQuerySpec(zones=battlefield, subtypes_all=("human",)), False),)
    else:
        expected = (
            (ObjectQuerySpec(zones=battlefield, types_all=("planeswalker",)), False),
            (ObjectQuerySpec(zones=battlefield, types_all=("creature",)), True),
        )
    if ((program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
            or len(program.handlers) != len(expected) + 1 or program.target_schema is not None
            or program.cost_schema is not None or program.effects):
        raise ValueError("Compound Hexproof requires a player grant and every original permanent grant")
    player, *permanents = program.handlers
    player_handler = PlayerTargetProtectionHandler()
    if player_handler.validate(player) != "hexproof":
        raise ValueError("Compound Hexproof requires player Hexproof")
    permanent_handler = FixedQueryKeywordGrantHandler()
    for item, (predicate, exclude_source) in zip(permanents, expected):
        node = permanent_handler.validate(item)
        if (node.target_controller != "source_controller" or node.predicate != predicate
                or node.exclude_source != exclude_source or node.abilities != ("Hexproof",) or node.fragments):
            raise ValueError("Compound Hexproof requires the original controlled permanent queries and source exclusion")
    return (*player_handler.capability_dependencies, *permanent_handler.capability_dependencies,
            "target.protection.hexproof_permanent")


def _controller_turn_player_and_source_hexproof_runtime_dependencies(program):
    from ..compiler.player_target_protection_nodes import TURN_PLAYER_AND_SOURCE_HEXPROOF_TEMPLATE

    if program.provenance.get("template_id") != TURN_PLAYER_AND_SOURCE_HEXPROOF_TEMPLATE:
        return ()
    from ..semantic_runtime.conditional_continuous import FixedPublicStateCharacteristicsHandler
    from ..semantic_runtime.player_target_protection import (
        CONTROLLER_TURN_PLAYER_HEXPROOF_CONDITION, ControllerTurnPlayerHexproofHandler,
    )

    if ((program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
            or len(program.handlers) != 2 or program.target_schema is not None
            or program.cost_schema is not None or program.effects or "recipient" in program.handlers[1]):
        raise ValueError("Turn-scoped Hexproof requires both player and source grants")
    player, source = program.handlers
    player_handler = ControllerTurnPlayerHexproofHandler()
    player_handler.validate(player)
    node = FixedPublicStateCharacteristicsHandler().validate(source)
    if (node.source_condition != CONTROLLER_TURN_PLAYER_HEXPROOF_CONDITION
            or node.target_kind != "source" or node.abilities != ("Hexproof",)
            or node.power or node.toughness or node.fragments):
        raise ValueError("Turn-scoped Hexproof requires the same current controller turn on the source")
    return (*player_handler.capability_dependencies, "target.protection.hexproof_permanent")


def _declaration_rule_runtime_requirements(program, required, blockers):
    """Aggregate independent declaration owners without changing their diagnostics."""
    from ..rules.attack_counter_unblockable import attack_counter_unblockable_runtime_dependencies
    from ..rules.counter_placement_blocking import counter_placement_blocking_program_dependencies
    from ..rules.echo_capability_shapes import discard_echo_runtime_dependencies
    from ..rules.source_counter_reflexive import source_counter_reflexive_runtime_dependencies
    from ..rules.third_resolved_source_trigger import third_source_trigger_runtime_dependencies
    from ..rules.temporary_all_blockers import temporary_all_blocker_runtime_dependencies
    from ..rules.match_outcome_restrictions import match_outcome_restriction_runtime_dependencies
    from ..rules.upkeep_game_win import upkeep_game_win_runtime_dependencies
    from ..rules.variable_library_selection_shapes import devotion_library_win_runtime_dependencies
    from ..rules.temporary_attack_requirements import temporary_attack_requirement_runtime_dependencies
    from ..rules.fixed_effect_clause_shapes import shared_damage_attack_runtime_dependencies
    from ..maximum_hand_size import linked_maximum_runtime_dependencies
    from ..rules.linked_entry_opponent import linked_entry_opponent_runtime_dependencies
    from ..rules.chosen_color_target_shapes import linked_color_protection_runtime_dependencies
    from ..rules.chosen_counter_placement import chosen_counter_placement_program_dependencies
    from ..rules.controlled_arrival_proliferate import controlled_arrival_proliferate_program_dependencies
    from ..rules.captured_draw_keyword_grant import captured_draw_keyword_grant_program_dependencies
    from ..rules.nonhuman_undying_anthem import nonhuman_undying_anthem_runtime_dependencies

    for resolver, label in (
        (source_block_pair_runtime_dependencies, 'source_block_pair'),
        (counter_placement_blocking_program_dependencies, "counter_placement_blocking"),
        (controlled_arrival_proliferate_program_dependencies, "controlled_arrival_proliferate"),
        (discard_echo_runtime_dependencies, "discard_echo"),
        (source_counter_reflexive_runtime_dependencies, 'source_counter_reflexive'),
        (third_source_trigger_runtime_dependencies, 'third_source_trigger'),
        (attack_counter_unblockable_runtime_dependencies, 'attack_counter_unblockable'),
        (fixed_blocker_runtime_dependencies, 'fixed_blocker'),
        (match_outcome_restriction_runtime_dependencies, "match_outcome_restriction"),
        (upkeep_game_win_runtime_dependencies, "upkeep_game_win"),
        (devotion_library_win_runtime_dependencies, "devotion_library_win"),
        (temporary_all_blocker_runtime_dependencies, 'temporary_all_blocker'),
        (temporary_attack_requirement_runtime_dependencies, 'temporary_attack_requirement'),
        (shared_damage_attack_runtime_dependencies, "shared_damage_attack_requirement"),
        (linked_entry_opponent_runtime_dependencies, "linked_entry_opponent"),
        (linked_color_protection_runtime_dependencies, "linked_color_protection"),
        (chosen_counter_placement_program_dependencies, "chosen_counter_placement"),
        (captured_draw_keyword_grant_program_dependencies, "captured_draw_keyword_grant"),
        (linked_maximum_runtime_dependencies, "linked_player_maximum"),
        (_attached_subtype_otherwise_runtime_dependencies, "attached_subtype_otherwise"),
        (_fixed_keywords_and_color_protection_runtime_dependencies, "fixed_keywords_and_color_protection"),
        (nonhuman_undying_anthem_runtime_dependencies, "nonhuman_undying_anthem"),
        (_turn_source_enchantment_creature_keywords_runtime_dependencies, "turn_source_enchantment_creature_keywords"),
        (_controller_player_and_permanent_hexproof_runtime_dependencies, "controller_player_and_permanent_hexproof"),
        (_controller_turn_player_and_source_hexproof_runtime_dependencies, "controller_turn_player_and_source_hexproof"),
    ):
        try:
            required.update(resolver(program))
        except (ValueError, TypeError) as exc:
            blockers.add(f'runtime_{label}:invalid:{exc}')


def _dynamic_reference_runtime_requirements(program):
    """Gather announced targets, live dealer references and public query minima."""
    required = set()
    blockers = set()
    from ..rules.damage_capability_shapes import dealt_damage_life_gain_runtime_dependencies
    try:
        required.update(dealt_damage_life_gain_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_dealt_life_gain:invalid:{exc}")
    from ..rules.optional_counter_capability_shapes import (
        controlled_combat_quest_counter_runtime_dependencies, owned_graveyard_quest_counter_runtime_dependencies,
    )
    for name, resolver in (
        ("owned_graveyard_quest_counter", owned_graveyard_quest_counter_runtime_dependencies),
        ("controlled_combat_quest_counter", controlled_combat_quest_counter_runtime_dependencies),
    ):
        try:
            required.update(resolver(program))
        except (TypeError, ValueError) as exc:
            blockers.add(f"runtime_{name}:invalid:{exc}")
    from ..rules.target_creature_power_results import target_creature_power_runtime_dependencies
    try:
        required.update(target_creature_power_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_target_creature_power:invalid:{exc}')
    from ..rules.cumulative_graveyard_upkeep import cumulative_graveyard_upkeep_runtime_dependencies
    try:required.update(cumulative_graveyard_upkeep_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:blockers.add(f'runtime_graveyard_upkeep:invalid:{exc}')
    from ..rules.attachment_source_return import attachment_source_return_runtime_dependencies
    try:required.update(attachment_source_return_runtime_dependencies(program))
    except (ValueError, TypeError) as exc:blockers.add(f'runtime_attachment_source_return:invalid:{exc}')
    from ..rules.cumulative_effect_cost_upkeep import cumulative_effect_cost_upkeep_runtime_dependencies
    try:required.update(cumulative_effect_cost_upkeep_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:blockers.add(f'runtime_upkeep_effect_cost:invalid:{exc}')
    from ..rules.cumulative_alternative_mana_upkeep import cumulative_alternative_mana_upkeep_runtime_dependencies
    try:required.update(cumulative_alternative_mana_upkeep_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:blockers.add(f'runtime_alternative_mana_upkeep:invalid:{exc}')
    from ..rules.cumulative_counter_upkeep import cumulative_counter_upkeep_runtime_dependencies
    try:required.update(cumulative_counter_upkeep_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:blockers.add(f'runtime_cumulative_counter_upkeep:invalid:{exc}')
    from ..rules.cumulative_object_upkeep import cumulative_object_upkeep_runtime_dependencies
    try:required.update(cumulative_object_upkeep_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:blockers.add(f'runtime_cumulative_object_upkeep:invalid:{exc}')
    from ..rules.source_counter_cast_value import source_counter_cast_runtime_dependencies
    try:required.update(source_counter_cast_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:blockers.add(f'runtime_source_counter_cast:invalid:{exc}')
    from ..rules.event_outcome_shapes import event_outcome_runtime_dependencies
    try: required.update(event_outcome_runtime_dependencies(program))
    except (TypeError,ValueError) as exc: blockers.add(f'runtime_event_outcome:invalid:{exc}')
    from ..rules.stack_target_change_shapes import single_stack_target_change_runtime_dependencies
    try:
        required.update(single_stack_target_change_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_stack_target_change:invalid:{exc}')
    from ..rules.stack_spell_copy_shapes import qualified_stack_copy_runtime_dependencies
    try:required.update(qualified_stack_copy_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:blockers.add(f'runtime_qualified_stack_copy:invalid:{exc}')
    from ..rules.combat_damage_library_access import combat_damage_library_program_dependencies
    from ..rules.fixed_counter_movement import fixed_counter_movement_program_dependencies
    from ..rules.departure_counter_transfer import departure_counter_transfer_program_dependencies
    from ..rules.targeted_opponent_library_top import opponent_top_exile_program_dependencies
    from ..rules.defending_player_top_exile import defending_player_top_exile_program_dependencies
    from ..rules.impulse_access_capability_shapes import shuffled_top_free_play_runtime_dependencies
    from ..rules.land_entry_library_exile import land_entry_top_exile_program_dependencies

    for owner, resolver in (
        ("defending_player_top_exile", defending_player_top_exile_program_dependencies),
        ("land_entry_top_exile", land_entry_top_exile_program_dependencies),
        ("combat_damage_library_access", combat_damage_library_program_dependencies),
        ("shuffled_top_free_play", shuffled_top_free_play_runtime_dependencies),
    ):
        try:
            required.update(resolver(program))
        except (TypeError, ValueError, KeyError) as exc:
            blockers.add(f"runtime_{owner}:invalid:{exc}")
    try:
        required.update(opponent_top_exile_program_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_opponent_top_exile:invalid:{exc}")
    try:
        required.update(fixed_counter_movement_program_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_fixed_counter_movement:invalid:{exc}")
    try:
        required.update(departure_counter_transfer_program_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_departure_counter_transfer:invalid:{exc}")
    try:
        required.update(announced_target_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_target_count:invalid:{exc}')
    from ..rules.fixed_controller_effect_shapes import player_history_life_loss_runtime_dependencies
    try:
        required.update(player_history_life_loss_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_player_history:invalid:{exc}')
    try:
        from ..rules.event_damage_sources import event_damage_source_runtime_dependencies
        required.update(event_damage_source_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_event_damage_source:invalid:{exc}')
    try:
        required.update(object_query_capabilities((program.effects,program.handlers,program.target_schema)))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_query:invalid:{exc}')
    return required, blockers


def _player_quantity_runtime_requirements(program,required,blockers):
    """Validate independently bound public player quantities and their frames."""
    from ..rules.current_life_effect_amounts import current_life_program_dependencies
    from ..rules.turn_life_loss_recovery import turn_life_loss_program_dependencies
    from ..rules.target_player_query_amounts import target_player_query_program_dependencies
    from ..rules.per_player_query_life import per_player_query_life_program_dependencies
    from ..rules.source_charge_life_totals import source_charge_life_program_dependencies
    from ..semantic_runtime.self_entry_counters import typed_dynamic_entry_counter_runtime_dependencies
    for resolver,label in ((current_life_program_dependencies,'current_life'),
            (typed_dynamic_entry_counter_runtime_dependencies, 'typed_dynamic_entry_counter'),
            (source_charge_life_program_dependencies, 'source_charge_life_total'),
            (turn_life_loss_program_dependencies,'turn_life_loss'),
            (target_player_query_program_dependencies,'target_player_query'),
            (per_player_query_life_program_dependencies,'per_player_query_life'),
            (target_hand_count_runtime_dependencies,'target_hand_count')):
        try:required.update(resolver(program))
        except (ValueError,TypeError) as exc:blockers.add(f'runtime_{label}:invalid:{exc}')


def _temporary_player_rule_requirements(program, required, blockers):
    from ..rules.next_spell_payment import (
        turn_color_spell_protection_runtime_dependencies, turn_spell_source_color_protection_runtime_dependencies,
    )

    from ..draw_redirect_model import temporary_draw_redirect_runtime_dependencies
    from ..rules.turn_creature_target_prohibition import turn_creature_target_prohibition_program_dependencies
    from ..rules.next_draw_replacement_shapes import next_draw_replacement_runtime_dependencies

    required.update(qualified_hexproof_runtime_dependencies(program))
    try:
        required.update(temporary_draw_redirect_runtime_dependencies(program))
        required.update(next_draw_replacement_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_temporary_draw_redirect:invalid:{exc}")
    try:
        required.update(temporary_cast_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_temporary_cast_restriction:invalid:{exc}")
    try:
        required.update(turn_color_spell_protection_runtime_dependencies(program))
        required.update(turn_spell_source_color_protection_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_turn_color_spell_protection:invalid:{exc}")
    try:
        required.update(turn_creature_target_prohibition_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_turn_creature_target_prohibition:invalid:{exc}")


def _action_permission_runtime_requirements(program, required, blockers):
    from ..kicker import positive_generic_kicker_runtime_dependencies
    from ..semantic_runtime.action_permissions import qualified_library_top_runtime_dependencies
    from ..semantic_runtime.mana_spending import tap_mana_multiplier_runtime_dependencies

    for resolver, label in (
        (qualified_library_top_runtime_dependencies, "qualified_library_top"),
        (positive_generic_kicker_runtime_dependencies, "positive_generic_kicker"),
        (tap_mana_multiplier_runtime_dependencies, "tap_mana_multiplier"),
    ):
        try:
            required.update(resolver(program))
        except (ValueError, TypeError) as exc:
            blockers.add(f"runtime_{label}:invalid:{exc}")


def _battlefield_runtime_requirements(program, required, blockers):
    from ..rules.activation.battlefield_grants import battlefield_grant_runtime_dependencies
    from ..end_combat_counterpart_runtime import end_combat_counterpart_runtime_dependencies
    from .impending import impending_program_dependencies
    from ..rules.damage_capability_shapes import (
        controller_damage_result_life_floor_runtime_dependencies, noncombat_prevention_life_runtime_dependencies,
        source_relative_damage_runtime_dependencies,
    )
    from ..rules.all_player_turn_rules import all_player_turn_rule_runtime_dependencies
    from ..rules.turn_match_outcome_protection import turn_match_outcome_runtime_dependencies
    from ..semantic_runtime.battlefield_cascade_grants import first_exile_cascade_runtime_dependencies
    from ..rules.characteristic_life_exchange import characteristic_life_exchange_program_dependencies
    from ..rules.graveyard_aura_entry import graveyard_aura_entry_runtime_dependencies
    from ..rules.player_life_exchange import player_life_exchange_runtime_dependencies
    from ..rules.random_activation_target import random_activation_target_runtime_dependencies
    from ..rules.die_roll_draw import die_draw_hand_size_runtime_dependencies
    from ..rules.random_opponent_damage import random_opponent_damage_runtime_dependencies
    from ..rules.current_combat_blocker_count import current_blocker_bonus_runtime_dependencies
    from ..rules.finite_damage_redirection import finite_damage_redirection_runtime_dependencies
    from ..rules.live_modifier_quantities import controller_turn_life_loss_modifier_runtime_dependencies
    from ..rules.temporary_ability_grant import (
        combat_prevention_draw_runtime_dependencies, temporary_combat_draw_unblockable_runtime_dependencies,
    )
    from ..rules.source_counter_kind_draw import source_counter_kind_draw_runtime_dependencies
    from ..rules.token_creation_capability_shapes import post_entry_copy_grant_runtime_dependencies
    from ..semantic_runtime.target_requirements import flagbearer_requirement_dependencies

    for resolve, label in (
        (combat_prevention_draw_runtime_dependencies, "combat_prevention_draw"),
        (source_counter_kind_draw_runtime_dependencies, "source_counter_kind_draw"),
        (post_entry_copy_grant_runtime_dependencies, "post_entry_copy_grant"),
        (temporary_combat_draw_unblockable_runtime_dependencies, "temporary_combat_draw_unblockable"),
        (flagbearer_requirement_dependencies, "flagbearer_requirement"),
        (battlefield_grant_runtime_dependencies, "battlefield_activation_grants"),
        (end_combat_counterpart_runtime_dependencies, "end_combat_counterpart"),
        (graveyard_aura_entry_runtime_dependencies, "graveyard_aura_entry"),
        (impending_program_dependencies, "impending"),
        (noncombat_prevention_life_runtime_dependencies, "static_noncombat_prevention_life"),
        (controller_damage_result_life_floor_runtime_dependencies, "controller_damage_result_life_floor"),
        (turn_match_outcome_runtime_dependencies, "turn_match_outcome_protection"),
        (first_exile_cascade_runtime_dependencies, "first_exile_cascade"),
        (all_player_turn_rule_runtime_dependencies, "all_player_turn_rules"),
        (controller_turn_life_loss_modifier_runtime_dependencies, "controller_turn_life_loss_modifier"),
        (source_relative_damage_runtime_dependencies, "source_relative_damage"),
        (finite_damage_redirection_runtime_dependencies, "finite_damage_redirection"),
        (die_draw_hand_size_runtime_dependencies, "single_d20_draw_hand_size"),
        (random_opponent_damage_runtime_dependencies, "random_opponent_damage"),
        (random_activation_target_runtime_dependencies, "random_activation_target"),
        (current_blocker_bonus_runtime_dependencies, "source_current_blocker_count"),
        (characteristic_life_exchange_program_dependencies, "characteristic_life_exchange"),
        (player_life_exchange_runtime_dependencies, "player_life_exchange"),
    ):
        try:
            required.update(resolve(program))
        except (TypeError, ValueError) as exc:
            blockers.add(f"runtime_{label}:invalid:{exc}")


def program_dynamic_runtime_requirements(program):
    from ..rules.target_land_type import target_land_type_runtime_dependencies
    from ..rules.graveyard_recycling import graveyard_recycling_runtime_dependencies
    from ..preparation import preparation_runtime_dependencies
    from ..rules.fuse_shapes import fuse_runtime_requirements
    try:
        fused=fuse_runtime_requirements(program)
        if fused is not None and 'fused_spell_resolution' in program.coverage:return fused
    except (TypeError,ValueError) as exc:return {'casting.method.fuse'},{f'runtime_fuse:invalid:{exc}'}
    required,blockers=_event_query_runtime_requirements(program)
    _battlefield_runtime_requirements(program, required, blockers)
    from ..rules.colored_hand_reveals import colored_hand_reveal_program_dependencies
    try:required.update(colored_hand_reveal_program_dependencies(program))
    except (ValueError,TypeError,KeyError) as exc:blockers.add(f'runtime_colored_hand_reveal:invalid:{exc}')
    _player_quantity_runtime_requirements(program,required,blockers)
    try:required.update(target_land_type_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_target_land_type:invalid:{exc}')
    try:required.update(graveyard_recycling_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_graveyard_recycling:invalid:{exc}')
    try:
        required.update(preparation_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_source_preparation:invalid:{exc}")
    required.update(_intrinsic_spell_face_dependencies(program.coverage))
    value_requirements, value_blockers = _value_runtime_requirements(program)
    required.update(value_requirements)
    blockers.update(value_blockers)
    try:required.update(city_activation_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_city_activation:invalid:{exc}')
    try:required.update(paid_graveyard_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_paid_graveyard:invalid:{exc}')
    if fused is not None:
        required.update(fused[0]);blockers.update(fused[1])
    try:required.update(nonmana_equip_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_nonmana_equip:invalid:{exc}')
    try:required.update(token_prowess_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_token_prowess:invalid:{exc}')
    try:required.update(compiled_emblem_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_emblem_program:invalid:{exc}')
    try:required.update(nonmana_buyback_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_nonmana_buyback:invalid:{exc}')
    _declaration_rule_runtime_requirements(program, required, blockers)
    from ..rules.occurrence_object_results import collect_occurrence_runtime_requirements

    collect_occurrence_runtime_requirements(program, required, blockers)
    try:required.update(keyword_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_attached_keyword:invalid:{exc}')
    _temporary_player_rule_requirements(program, required, blockers)
    _action_permission_runtime_requirements(program, required, blockers)
    try:required.update(random_graveyard_return_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_random_graveyard_return:invalid:{exc}')
    _zone_move_runtime_requirements(program, required, blockers)
    try:required.update(closed_effect_program_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_closed_effect_program:invalid:{exc}')
    try:required.update(activation_restriction_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_boast:invalid:{exc}')
    try:
        required.update(attached_protection_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_attached_protection:invalid:{exc}')
    try:
        required.update(provoke_program_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_provoke:invalid:{exc}')
    try:
        required.update(paid_mana_resolution_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_paid_mana_resolution:invalid:{exc}')
    try:
        required.update(paid_color_entry_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_paid_color_entry:invalid:{exc}')
    try:
        required.update(reconfigure_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_reconfigure:invalid:{exc}')
    cost_required, cost_blockers = _cost_metric_runtime_requirements(program)
    required.update(cost_required)
    blockers.update(cost_blockers)
    try:
        required.update(source_fixed_protection_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_source_fixed_protection:invalid:{exc}')
    try:
        required.update(source_protection_choice_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_source_protection_choice:invalid:{exc}')
    try:
        required.update(source_keyword_choice_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_source_keyword_choice:invalid:{exc}')
    try:
        required.update(activation_mana_option_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_activation_mana_options:invalid:{exc}')
    try:
        required.update(any_source_counter_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_any_source_counter_cost:invalid:{exc}')
    try:
        required.update(selected_counter_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_selected_counter_cost:invalid:{exc}')
    try:
        required.update(characteristic_set_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_target_characteristics:invalid:{exc}')
    try:
        required.update(mana_color_amount_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_spent_mana_color_amount:invalid:{exc}')
    try:
        required.update(multikicker_amount_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_multikicker_amount:invalid:{exc}')
    try:
        required.update(self_target_reduction_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_self_target_reduction:invalid:{exc}')
    reference_required, reference_blockers = _dynamic_reference_runtime_requirements(program)
    required.update(reference_required)
    blockers.update(reference_blockers)
    try:
        required.update(announced_additional_cost_dependencies(program.cost_schema))
        from ..rules.colored_hand_credit import colored_hand_credit_dependencies
        from ..rules.fixed_payment_groups import fixed_discard_sacrifice_dependencies
        required.update(fixed_discard_sacrifice_dependencies(program.cost_schema))
        required.update(colored_hand_credit_dependencies(program.cost_schema))
        from ..rules.random_discard_additional_cost import random_spell_cost_dependencies
        required.update(random_spell_cost_dependencies(program.cost_schema))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_cast_cost:invalid:{exc}')
    from ..rules.opponent_caster_discard_payment import caster_discard_payment_program_dependencies

    try:
        required.update(caster_discard_payment_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_caster_discard_payment:invalid:{exc}")
    try:
        required.update(bound_resolution_condition_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_same_target_replacement:invalid:{exc}')
    try:
        required.update(temporary_keyword_removal_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_temporary_keyword_removal:invalid:{exc}')
    try:
        required.update(increment_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_increment:invalid:{exc}')
    return required,blockers


def _source_position_and_graveyard_requirements(program):
    from ..rules.artifact_graveyard_cast_activation import artifact_graveyard_cast_runtime_dependencies
    from ..rules.loot_transform_graveyard import loot_transform_runtime_dependencies
    from ..rules.loyalty_graveyard_cast import loyalty_graveyard_cast_runtime_dependencies
    from ..rules.owner_library_position_shapes import owner_library_position_runtime_dependencies

    return (*owner_library_position_runtime_dependencies(program), *loot_transform_runtime_dependencies(program),
            *loyalty_graveyard_cast_runtime_dependencies(program),
            *artifact_graveyard_cast_runtime_dependencies(program))
