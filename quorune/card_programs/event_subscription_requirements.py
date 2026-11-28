"""Normalize printed event subscriptions and their runtime capability requirements."""

from ..crime_model import CRIME_EVENT,CRIME_CAPABILITY,crime_fact_dependencies
from ..rules.event_subscriptions import (
    FixedEventSubscriptionSet, LAND_PLAY_EVENT, controller_keyword_absence_runtime_dependencies,
    death_keyword_absence_runtime_dependencies, dungeon_completion_dependencies,
    face_transition_event_dependencies, upkeep_hand_runtime_dependencies,
)


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
        commander_intervening_runtime_dependencies,
        entrant_land_advantage_runtime_dependencies, graveyard_creature_transition_runtime_dependencies,
        opponent_attack_draw_cast_runtime_dependencies,
        greatest_artifact_upkeep_runtime_dependencies,
        physical_card_play_runtime_dependencies,
    )
    from ..rules.attached_greatest_power_condition import attached_greatest_power_runtime_dependencies
    from ..rules.attached_temporary_control import attached_temporary_control_runtime_dependencies
    from ..rules.announced_x_damage_hand_cast import announced_x_damage_hand_cast_program_dependencies
    from ..rules.bounded_hand_cast import (
        damaged_player_hand_cast_runtime_dependencies, legendary_wizard_hand_cast_runtime_dependencies,
    )
    from ..rules.source_upkeep_handoff import source_control_or_conversion_runtime_dependencies
    from ..rules.entrant_name_absence import entrant_name_absence_runtime_dependencies
    from ..rules.monarch_transition import monarch_became_runtime_dependencies
    from ..combat_entry_activations import ninjutsu_activation_trigger_dependencies
    from ..rules.creature_ability_activation import creature_ability_activation_runtime_dependencies
    from ..rules.gift_occurrence import gift_observer_runtime_dependencies
    from ..rules.nonmana_activation_copy import nonmana_activation_copy_runtime_dependencies
    from ..rules.opponent_library_exile_triggers import library_exile_trigger_runtime_dependencies
    from ..rules.opposing_owner_entry_life import opposing_owner_entry_runtime_dependencies
    from ..rules.draw_step_drawing_settlement import draw_step_settlement_program_dependencies
    from ..rules.trigger_source_information import tapped_draw_damage_runtime_dependencies
    from ..rules.subtype_damage_controller import subtype_damage_program_dependencies
    from ..rules.source_power_opponent_damage import source_power_opponent_damage_program_dependencies
    from ..rules.target_announcements import source_attack_or_spell_target_runtime_dependencies
    from ..rules.exile_origin_creature_counters import exile_origin_counter_runtime_dependencies
    try:
        required.update(draw_step_settlement_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f'runtime_draw_step_settlement:invalid:{exc}')
    from ..rules.controlled_spell_counter import controlled_spell_counter_runtime_dependencies
    try:
        subscriptions=FixedEventSubscriptionSet.from_condition(program.event_condition)
        from ..rules.night_shift_dice import night_shift_runtime_dependencies
        from ..rules.retained_die_roll import retained_dice_runtime_dependencies

        required.update(retained_dice_runtime_dependencies(program))
        required.update(night_shift_runtime_dependencies(program))
        required.update(counter_placement_event_dependencies(program, subscriptions))
        required.update(player_result_runtime_dependencies(program, subscriptions))
        required.update(spell_cast_event_runtime_dependencies(program, subscriptions))
        from ..rules.opponent_library_shuffle import opponent_shuffle_runtime_dependencies

        from ..rules.opponent_library_search import opponent_library_search_runtime_dependencies

        required.update(opponent_shuffle_runtime_dependencies(program))
        required.update(opponent_library_search_runtime_dependencies(program))
        required.update(physical_card_play_runtime_dependencies(program))
        required.update(graveyard_creature_transition_runtime_dependencies(program, subscriptions))
        required.update(opponent_attack_draw_cast_runtime_dependencies(program, subscriptions))
        from ..rules.opposing_end_step_advantage import opposing_end_step_runtime_dependencies
        required.update(opposing_end_step_runtime_dependencies(program))
        required.update(entrant_land_advantage_runtime_dependencies(program))
        from ..rules.effect_node_capabilities import main_phase_extra_combat_program_dependencies

        required.update(main_phase_extra_combat_program_dependencies(program))
        required.update(attached_greatest_power_runtime_dependencies(program))
        required.update(attached_temporary_control_runtime_dependencies(program))
        required.update(legendary_wizard_hand_cast_runtime_dependencies(program))
        required.update(announced_x_damage_hand_cast_program_dependencies(program))
        required.update(source_control_or_conversion_runtime_dependencies(program))
        required.update(damaged_player_hand_cast_runtime_dependencies(program))
        required.update(entrant_name_absence_runtime_dependencies(program))
        required.update(monarch_became_runtime_dependencies(program))
        required.update(ninjutsu_activation_trigger_dependencies(program, subscriptions))
        required.update(creature_ability_activation_runtime_dependencies(program, subscriptions))
        required.update(gift_observer_runtime_dependencies(program, subscriptions))
        required.update(nonmana_activation_copy_runtime_dependencies(program, subscriptions))
        required.update(library_exile_trigger_runtime_dependencies(program))
        required.update(opposing_owner_entry_runtime_dependencies(program))
        required.update(tapped_draw_damage_runtime_dependencies(program))
        required.update(subtype_damage_program_dependencies(program))
        required.update(source_power_opponent_damage_program_dependencies(program))
        required.update(source_attack_or_spell_target_runtime_dependencies(program))
        required.update(exile_origin_counter_runtime_dependencies(program))
        required.update(controlled_spell_counter_runtime_dependencies(program, subscriptions))
        from ..saga_lifecycle import final_saga_chapter_runtime_dependencies

        required.update(final_saga_chapter_runtime_dependencies(program, subscriptions))
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
        required.update(commander_intervening_runtime_dependencies(program))
        required.update(controller_keyword_absence_runtime_dependencies(program))
        required.update(greatest_artifact_upkeep_runtime_dependencies(program))
        required.update(death_keyword_absence_runtime_dependencies(program))
        required.update(face_transition_event_dependencies(program, subscriptions))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_event_subscriptions:invalid:{exc}')
    return required,blockers


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
