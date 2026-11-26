"""Subscription-derived runtime coverage for generated trigger programs."""
from __future__ import annotations

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..attack_batch_model import ATTACHED_PLAYER_ATTACK_VARIANT, DECLARED_ATTACK_POWER_COVERAGE
from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
from ..controller_creature_keyword_absence import (
    CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE, ControllerCreatureKeywordAbsence,
)
from ..death_keyword_absence import DEATH_KEYWORD_ABSENCE_COVERAGE, DeathKeywordAbsence
from ..day_night_model import DAY_TOGGLE_COUNTER_VARIANT
from ..life_change import FIRST_LIFE_GAIN_EVENT
from ..land_entry_condition_model import (
    ENTRANT_LAND_ADVANTAGE_COVERAGE, OTHER_LAND_ARRIVAL_COVERAGE, OTHER_LAND_ARRIVAL_VARIANT,
    uses_entrant_land_advantage,
)
from ..event_spell_copy_model import EVENT_SPELL_COPY_MECHANIC
from ..other_spell_history_model import OTHER_SPELL_CAST_MECHANIC, is_other_spell_self_cast_condition
from ..preparation_model import PREPARATION_RESULT_MECHANIC
from ..rules.attacked_player_life import ATTACKED_PLAYER_LIFE_COVERAGE
from ..rules.attacking_opponent_library_exile import ATTACKED_PLAYER_OR_WALKER_VARIANT
from ..rules.combat_damage_entry_history import COMBAT_DAMAGE_ENTRY_COVERAGE, is_combat_damage_entry_condition
from ..rules.entrant_name_absence import ENTRANT_NAME_ABSENCE_CAPABILITY, ENTRANT_NAME_ABSENCE_COVERAGE
from ..rules.transform_capability_shapes import TRIGGERED_SOURCE_TRANSFORM_MECHANIC
from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_COVERAGE


def fixed_typed_result_requires_current_ability(binding, template, required):
    from ..entry_designations import LINKED_PLAYER_CAST_MILL_TEMPLATE, LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE
    from ..rules.counter_placement_blocking import COUNTER_PLACEMENT_BLOCKING_TEMPLATE
    from ..power_sacrifice_model import (
        ATTACK_SACRIFICE_STAT_TEMPLATE, CONTROLLER_SACRIFICE_LIFE_TEMPLATE, ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE,
    )
    from .bounded_hand_cast_templates import LEGENDARY_WIZARD_HAND_CAST_TEMPLATE
    from .fixed_source_combat_growth import FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS
    from ..rules.attached_copy_payment import TEMPLATE as ATTACHED_COPY_PAYMENT_TEMPLATE

    return required and (
        template in FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS
        or binding.variant == "fixed_entry_return_requirement"
        or template in {
            LEGENDARY_WIZARD_HAND_CAST_TEMPLATE, COUNTER_PLACEMENT_BLOCKING_TEMPLATE,
            "scheduled-player-hand-difference-v1", "linked-entry-opponent-upkeep-damage-v1",
            "fixed-counter-upkeep-target-hand-life-v1", "upkeep-fixed-other-controlled-creature-damage-v1",
            CONTROLLER_SACRIFICE_LIFE_TEMPLATE, ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE, ATTACK_SACRIFICE_STAT_TEMPLATE,
            LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE, LINKED_PLAYER_CAST_MILL_TEMPLATE, ATTACHED_COPY_PAYMENT_TEMPLATE,
        }
    )


def trigger_runtime_coverage(binding, *, current_ability, qualified_zone_variant, aggregated_zone_variant, ability_word_public_event_variants, public_event_variants, counter_event_variants, one_or_more_variants, multi_event_variants, effect_mechanics=(), result_template_id=None) -> tuple[str, ...]:
    values: list[str] = []
    from .delayed_object_templates import DELAYED_SOURCE_CONVERT_TEMPLATE
    from .recipient_control_templates import SOURCE_UPKEEP_HANDOFF_TEMPLATE
    from ..rules.die_roll_draw import MECHANIC as DIE_DRAW_HAND_SIZE_MECHANIC
    from ..rules.trigger_source_information import UNTAPPED_DAMAGE_DRAW_COVERAGE, UNTAPPED_DAMAGE_DRAW_VARIANT
    from ..rules.opposing_death_delayed_return import OPPOSING_DEATH_DELAYED_RETURN_MECHANIC
    if result_template_id in {SOURCE_UPKEEP_HANDOFF_TEMPLATE, DELAYED_SOURCE_CONVERT_TEMPLATE}:
        current_ability = True
    if result_template_id == SOURCE_UPKEEP_HANDOFF_TEMPLATE:
        values.append(SPELL_HISTORY_TRANSFORM_COVERAGE)
    from ..rules.draw_step_drawing_settlement import DRAW_STEP_SETTLEMENT_MECHANIC

    if DRAW_STEP_SETTLEMENT_MECHANIC in effect_mechanics:
        current_ability = True
    if DIE_DRAW_HAND_SIZE_MECHANIC in effect_mechanics:
        current_ability = True

    if binding.variant == UNTAPPED_DAMAGE_DRAW_VARIANT:
        values.append(UNTAPPED_DAMAGE_DRAW_COVERAGE)
    if (binding.event.value == FIRST_LIFE_GAIN_EVENT or PREPARATION_RESULT_MECHANIC in effect_mechanics
            or OPPOSING_DEATH_DELAYED_RETURN_MECHANIC in effect_mechanics
            or binding.variant in {"source_graveyard_arrival_shuffle", "unpaid_creature_entry_group"}):
        current_ability = True
    from ..additional_beginning_model import ADDITIONAL_BEGINNING_VARIANTS
    from ..rules.monarch_transition import MONARCH_BECAME_VARIANT

    if binding.variant in ADDITIONAL_BEGINNING_VARIANTS or binding.variant == MONARCH_BECAME_VARIANT:
        current_ability = True
    from ..rules.combat_point_counters import COMBAT_POINT_COUNTER_CAPABILITY

    if COMBAT_POINT_COUNTER_CAPABILITY in binding.public_capabilities:
        current_ability = True
    if binding.variant in {ATTACHED_PLAYER_ATTACK_VARIANT, ATTACKED_PLAYER_OR_WALKER_VARIANT, DAY_TOGGLE_COUNTER_VARIANT}:
        current_ability = True
    if current_ability or binding.variant in {qualified_zone_variant, aggregated_zone_variant, "damage_human_destroy"} or binding.variant in {'your first main phase', 'your precombat main phase', 'your second main phase', 'your postcombat main phase', *ability_word_public_event_variants, *public_event_variants - counter_event_variants, 'class_level_changed'}:
        values.append(CURRENT_ABILITY_FRAGMENT_COVERAGE)
    from ..rules.event_subscriptions import CONTROLLER_FOOD_SACRIFICE_GROUP_CAPABILITY

    if binding.variant in one_or_more_variants or CONTROLLER_FOOD_SACRIFICE_GROUP_CAPABILITY in binding.public_capabilities:
        values.append('one_or_more_event_batch')
    if binding.variant in multi_event_variants:
        values.append('fixed_multi_event_subscription')
    if getattr(getattr(binding, "spell_subject", None), "requires_no_mana_spent", False):
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition"))
    if EVENT_SPELL_COPY_MECHANIC in effect_mechanics and is_other_spell_self_cast_condition(binding.event_condition):
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition", OTHER_SPELL_CAST_MECHANIC))
    from ..rules.departure_counter_transfer import DEPARTURE_COUNTER_TRANSFER_CAPABILITY
    if DEPARTURE_COUNTER_TRANSFER_CAPABILITY in binding.public_capabilities:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition"))
    if ENTRANT_NAME_ABSENCE_CAPABILITY in binding.public_capabilities:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition", ENTRANT_NAME_ABSENCE_COVERAGE))
    if is_combat_damage_entry_condition(binding.event_condition):
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition", COMBAT_DAMAGE_ENTRY_COVERAGE))
    if binding.variant == OTHER_LAND_ARRIVAL_VARIANT:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition", OTHER_LAND_ARRIVAL_COVERAGE))
    if uses_entrant_land_advantage(binding.event_condition):
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition", ENTRANT_LAND_ADVANTAGE_COVERAGE))
    if binding.public_state_condition is not None:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, 'intervening_condition', FIXED_PUBLIC_STATE_INTERVENING_COVERAGE))
        if binding.event.value == "step.begin":
            from ..rules.fixed_effect_payment_shapes import (
                SOURCE_COUNTER_UPKEEP_DRAW_COVERAGE, source_counter_upkeep_draw_condition,
            )

            if binding.public_state_condition == source_counter_upkeep_draw_condition():
                values.append(SOURCE_COUNTER_UPKEEP_DRAW_COVERAGE)
    if isinstance(binding.public_state_condition, ControllerCreatureKeywordAbsence):
        values.append(CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE)
    if isinstance(binding.public_state_condition, DeathKeywordAbsence):
        values.append(DEATH_KEYWORD_ABSENCE_COVERAGE)
    if binding.attacked_player_life:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, 'intervening_condition', ATTACKED_PLAYER_LIFE_COVERAGE))
    if binding.minimum_declared_attack_power is not None:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, 'intervening_condition', DECLARED_ATTACK_POWER_COVERAGE))
    if TRIGGERED_SOURCE_TRANSFORM_MECHANIC in effect_mechanics:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, SPELL_HISTORY_TRANSFORM_COVERAGE))
    return tuple(dict.fromkeys(values))
