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
from ..rules.closed_effect_program_shapes import closed_effect_program_runtime_dependencies
from ..rules.spell_self_exile import spell_self_exile_runtime_dependencies, spell_self_shuffle_runtime_dependencies
from ..rules.random_graveyard_returns import random_graveyard_return_runtime_dependencies
from ..rules.temporary_cast_restrictions import temporary_cast_runtime_dependencies
from ..rules.keyword_runtime_requirements import keyword_runtime_dependencies
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
    from ..rules.attachment_actions import FIXED_NONMANA_EQUIP_CAPABILITY
    for ability in activated_abilities_from_descriptors(program.handlers):
        if ability.builtin_semantic_key == 'builtin:equip' and (
            ability.life_payment or ability.energy_payment or ability.choices
            or ability.source_counter_removal_cost is not None
        ):
            return (FIXED_NONMANA_EQUIP_CAPABILITY,)
    return ()


def _cost_metric_runtime_requirements(program):
    required, blockers = set(), set()
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
    from ..rules.activation.granting_attachment import granting_attachment_runtime_dependencies
    try:
        required.update(granting_attachment_runtime_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_granting_attachment_payment:invalid:{exc}')
    return required, blockers


def _event_query_runtime_requirements(program):
    """Infer descriptor-owned subscriptions and normalized event queries."""
    from ..public_history_query_model import public_history_query_dependencies
    from ..spell_history_ordinal_model import qualified_spell_ordinal_dependencies
    from ..rules.qualified_attacks import qualified_attack_runtime_dependencies
    from ..saddle import saddle_activation_runtime_dependencies, saddled_attack_runtime_dependencies
    from ..rules.entry_permanent_results import entry_animation_runtime_dependencies
    from ..rules.unblocked_attackers import unblocked_attacker_runtime_dependencies
    from ..rules.block_characteristics import qualified_block_runtime_dependencies
    from ..rules.attack_batches import qualified_attack_batch_runtime_dependencies
    from ..rules.attacked_player_life import attacked_player_life_runtime_dependencies
    from ..rules.event_player_result_shapes import event_player_damage_runtime_dependencies
    from ..rules.scheduled_turn_skip_shapes import scheduled_turn_skip_event_dependencies
    from ..rules.combat_defending_players import combat_defending_player_runtime_dependencies
    from ..rules.event_player_targets import event_player_target_runtime_dependencies
    from ..rules.per_player_target_sets import per_player_target_runtime_dependencies
    from ..rules.reanimation_entry_counters import reanimation_entry_counter_runtime_dependencies
    from ..rules.finality_counters import finality_counter_runtime_dependencies
    from ..rules.target_set_result_sequences import target_set_result_sequence_runtime_dependencies
    from ..rules.target_player_sequences import target_player_sequence_runtime_dependencies
    from ..rules.stack_controller_payment_shapes import stack_controller_payment_scope_dependencies
    from ..rules.fixed_effect_payment_shapes import fixed_payment_event_player_runtime_dependencies
    from ..rules.reflexive_payment_shapes import reflexive_payment_runtime_dependencies
    from ..cast_event_amounts import cast_mana_value_runtime_dependencies
    from ..rules.event_spell_copy import event_spell_copy_runtime_dependencies
    from ..rules.casting.spell_mana_payments import expend_runtime_dependencies
    from ..rules.modal_node_capabilities import modal_event_runtime_dependencies
    from ..rules.fixed_resolution_mana_shapes import fixed_resolution_mana_runtime_dependencies
    from ..rules.special_node_shapes import special_result_trigger_runtime_dependencies
    from ..rules.state_triggers import state_trigger_runtime_dependencies
    from ..rules.received_damage_events import received_damage_runtime_dependencies
    from ..rules.designation_events import designation_event_runtime_dependencies
    from ..source_entry_cast_information import source_entry_cast_program_dependencies
    from ..rules.graveyard_return_choices import graveyard_return_choice_runtime_dependencies
    from ..rules.scheduled_player_shapes import scheduled_player_departure_runtime_dependencies
    required,blockers=set(),set()
    from ..entry_x_amount_model import entry_x_amount_runtime_dependencies
    try:required.update(entry_x_amount_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_entry_x_amount:invalid:{exc}')
    from ..ravenous_model import ravenous_runtime_dependencies
    try:required.update(ravenous_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_ravenous:invalid:{exc}')
    from ..rules.graveyard_self_return import graveyard_self_return_runtime_dependencies
    try:required.update(graveyard_self_return_runtime_dependencies(program))
    except (ValueError,TypeError,KeyError) as exc:blockers.add(f'runtime_graveyard_source_return:invalid:{exc}')
    from ..rules.spell_owner_targets import spell_owner_target_dependencies
    try:required.update(spell_owner_target_dependencies(getattr(program,'target_schema',None)))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_spell_owner_target:invalid:{exc}')
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
    from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
    from ..counter_placement_event_model import counter_placement_event_dependencies
    from ..rules.event_subscriptions import FixedEventSubscriptionSet, LAND_PLAY_EVENT, upkeep_hand_runtime_dependencies
    try:
        subscriptions=FixedEventSubscriptionSet.from_condition(program.event_condition)
        required.update(counter_placement_event_dependencies(program, subscriptions))
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
            if any(child.event == 'spell.cast' for child in subscriptions.subscriptions):
                required.add('trigger.event.normalized_spell_cast')
            if any(child.event.removesuffix('.self') == 'permanent.enter' for child in subscriptions.subscriptions):
                required.add('trigger.event.normalized_zone_change')
            if any(child.event.removesuffix('.self')==MUTATE_EVENT for child in subscriptions.subscriptions):
                required.add(MUTATE_CAPABILITY)
        required.update(upkeep_hand_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_event_subscriptions:invalid:{exc}')
    for resolver,value,label in (
        (public_history_query_dependencies,program.event_condition,'public_history'),
        (qualified_spell_ordinal_dependencies,program.event_condition,'qualified_spell_ordinal'),
        (qualified_attack_runtime_dependencies,program,'qualified_attack'),
        (saddle_activation_runtime_dependencies,program,'saddle_activation'),
        (entry_animation_runtime_dependencies,program,'entry_animation'),
        (saddled_attack_runtime_dependencies,program,'saddled_attack'),
        (unblocked_attacker_runtime_dependencies,program,'unblocked_attacker'),
        (qualified_block_runtime_dependencies,program,'qualified_block'),
        (qualified_attack_batch_runtime_dependencies,program,'qualified_attack_batch'),
        (attacked_player_life_runtime_dependencies,program,'attacked_player_life'),
        (event_player_damage_runtime_dependencies,program,'event_player_damage'),
        (scheduled_turn_skip_event_dependencies,program,'scheduled_turn_skip_player'),
        (combat_defending_player_runtime_dependencies,program,'combat_defending_player'),
        (event_player_target_runtime_dependencies,program,'event_player_target'),
        (per_player_target_runtime_dependencies,program,'per_player_targets'),
        (reanimation_entry_counter_runtime_dependencies,program,'reanimation_entry_counters'),
        (finality_counter_runtime_dependencies,program,'finality_counter'),
        (target_set_result_sequence_runtime_dependencies,program,'target_set_result_sequence'),
        (target_player_sequence_runtime_dependencies,program,'target_player_sequence'),
        (stack_controller_payment_scope_dependencies,program,'stack_controller_payment_scope'),
        (fixed_payment_event_player_runtime_dependencies,program,'payment_event_player'),
        (reflexive_payment_runtime_dependencies,program,'reflexive_payment'),
        (cast_mana_value_runtime_dependencies,program,'cast_spell_mana_value'),
        (event_spell_copy_runtime_dependencies,program,'event_spell_copy'),
        (expend_runtime_dependencies,program,'expend'),
        (modal_event_runtime_dependencies,program,'modal_event'),
        (fixed_resolution_mana_runtime_dependencies,program,'fixed_resolution_mana'),
        (special_result_trigger_runtime_dependencies,program,'special_result_trigger'),
        (state_trigger_runtime_dependencies,program,'state_trigger'),
        (received_damage_runtime_dependencies,program,'received_damage'),
        (designation_event_runtime_dependencies,program,'designation_event'),
        (source_entry_cast_program_dependencies,program,'source_entry_cast'),
        (graveyard_return_choice_runtime_dependencies,program,'graveyard_return_choice'),
        (scheduled_player_departure_runtime_dependencies,program,'scheduled_player_departure'),
    ):
        try:required.update(resolver(value))
        except (ValueError,TypeError) as exc:blockers.add(f'runtime_{label}:invalid:{exc}')
    return required,blockers


def _intrinsic_spell_face_dependencies(coverage):
    return {capability for marker, capability in (
        (ADVENTURE_COVERAGE, ADVENTURE_CAPABILITY), (OMEN_COVERAGE, OMEN_CAPABILITY),
    ) if marker in coverage}


def _value_runtime_requirements(program):
    """Infer the existing minima for typed values and their public predicates."""
    from ..characteristic_fragments import characteristic_quantity_capabilities
    from ..rules.casting.public_conditions import self_public_condition_dependencies
    from ..rules.casting_additional_costs import fixed_creature_sacrifice_price
    from ..scalar_effect_amount_model import PAID_SACRIFICE_STAT_CAPABILITY, PAID_SACRIFICE_STAT_MECHANIC

    values = (program.effects, program.handlers, program.target_schema, program.cost_schema)
    required = set(characteristic_quantity_capabilities((*values, program.event_condition)))
    blockers = set()
    try:
        required.update(next_turn_effect_dependencies(program.effects))
    except ValueError as exc:
        blockers.add(f"runtime_next_turn_duration:invalid:{exc}")
    from ..source_tap_effect import source_tap_effect_dependencies
    try:
        required.update(source_tap_effect_dependencies(program.effects))
    except ValueError as exc:
        blockers.add(f"runtime_source_tap_duration:invalid:{exc}")
    try:
        required.update(self_public_condition_dependencies((program.handlers, program.cost_schema)))
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_self_public_condition:invalid:{exc}")
    try:
        amounts = typed_result_amount_runtime_dependencies(values, mechanics=tuple(program.coverage))
        from ..fractional_quantity_model import HALF_MILL_CAPABILITY
        from ..target_cardinality import SELECTED_PLAYER_RESULT_CAPABILITY
        if SELECTED_PLAYER_RESULT_CAPABILITY in amounts:
            from ..rules.any_number_target_shapes import selected_player_result_node_capabilities
            if not selected_player_result_node_capabilities(
                effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
            ):
                raise ValueError("Selected player results require their closed any-number target program")
        if HALF_MILL_CAPABILITY in amounts:
            from ..rules.mill_capability_shapes import validate_half_mill_program_scope
            validate_half_mill_program_scope(program)
        paid = PAID_SACRIFICE_STAT_CAPABILITY in amounts
        if paid != (PAID_SACRIFICE_STAT_MECHANIC in program.coverage) or (paid and (
            program.event != 'resolve' or not program.ability_id.startswith('spell:')
            or not fixed_creature_sacrifice_price(program.cost_schema)
        )):
            raise ValueError('Paid creature stats require their closed spell and fixed creature sacrifice price')
        required.update(amounts)
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_typed_result_amount:invalid:{exc}")
    try:
        required.update(fixed_resolution_characteristics_runtime_dependencies(program))
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_resolution_characteristics:invalid:{exc}")
    try:
        required.update(cast_x_runtime_dependencies(values))
    except (ValueError, TypeError) as exc:
        blockers.add(f"runtime_cast_x_amount:invalid:{exc}")
    return required, blockers


def _terminal_spell_move_requirements(program, required, blockers):
    for resolver, label in (
        (spell_self_exile_runtime_dependencies, 'spell_self_exile'),
        (spell_self_shuffle_runtime_dependencies, 'spell_self_shuffle'),
    ):
        try:
            required.update(resolver(program))
        except (ValueError, TypeError) as exc:
            blockers.add(f'runtime_{label}:invalid:{exc}')


def program_dynamic_runtime_requirements(program):
    from ..rules.fuse_shapes import fuse_runtime_requirements
    try:
        fused=fuse_runtime_requirements(program)
        if fused is not None and 'fused_spell_resolution' in program.coverage:return fused
    except (TypeError,ValueError) as exc:return {'casting.method.fuse'},{f'runtime_fuse:invalid:{exc}'}
    required,blockers=_event_query_runtime_requirements(program)
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
    try:required.update(source_block_pair_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_source_block_pair:invalid:{exc}')
    try:required.update(fixed_blocker_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_fixed_blocker:invalid:{exc}')
    from ..rules.temporary_all_blockers import temporary_all_blocker_runtime_dependencies
    try:required.update(temporary_all_blocker_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_temporary_all_blocker:invalid:{exc}')
    from ..rules.occurrence_object_results import occurrence_object_runtime_dependencies
    try:required.update(occurrence_object_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_occurrence_object:invalid:{exc}')
    try:required.update(keyword_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_attached_keyword:invalid:{exc}')
    try:required.update(temporary_cast_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_temporary_cast_restriction:invalid:{exc}')
    try:required.update(random_graveyard_return_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_random_graveyard_return:invalid:{exc}')
    _terminal_spell_move_requirements(program, required, blockers)
    try:required.update(closed_effect_program_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_closed_effect_program:invalid:{exc}')
    try:required.update(activation_restriction_runtime_dependencies(program))
    except (ValueError,TypeError) as exc:blockers.add(f'runtime_boast:invalid:{exc}')
    required.update(qualified_hexproof_runtime_dependencies(program))
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
    try:
        required.update(announced_target_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_target_count:invalid:{exc}')
    try:
        required.update(object_query_capabilities((program.effects,program.handlers,program.target_schema)))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_query:invalid:{exc}')
    try:
        required.update(announced_additional_cost_dependencies(program.cost_schema))
        from ..rules.random_discard_additional_cost import random_spell_cost_dependencies
        required.update(random_spell_cost_dependencies(program.cost_schema))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_cast_cost:invalid:{exc}')
    try:
        required.update(bound_resolution_condition_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_same_target_replacement:invalid:{exc}')
    try:
        required.update(target_hand_count_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_target_hand_count:invalid:{exc}')
    try:
        required.update(temporary_keyword_removal_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_temporary_keyword_removal:invalid:{exc}')
    try:
        required.update(increment_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_increment:invalid:{exc}')
    return required,blockers
