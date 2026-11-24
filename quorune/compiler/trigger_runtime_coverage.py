"""Subscription-derived runtime coverage for generated trigger programs."""
from __future__ import annotations

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..attack_batch_model import DECLARED_ATTACK_POWER_COVERAGE
from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
from ..controller_creature_keyword_absence import (
    CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE, ControllerCreatureKeywordAbsence,
)
from ..death_keyword_absence import DEATH_KEYWORD_ABSENCE_COVERAGE, DeathKeywordAbsence
from ..life_change import FIRST_LIFE_GAIN_EVENT
from ..event_spell_copy_model import EVENT_SPELL_COPY_MECHANIC
from ..other_spell_history_model import OTHER_SPELL_CAST_MECHANIC, is_other_spell_self_cast_condition
from ..preparation_model import PREPARATION_RESULT_MECHANIC
from ..rules.attacked_player_life import ATTACKED_PLAYER_LIFE_COVERAGE
from ..rules.combat_damage_entry_history import COMBAT_DAMAGE_ENTRY_COVERAGE, is_combat_damage_entry_condition
from ..rules.transform_capability_shapes import TRIGGERED_SOURCE_TRANSFORM_MECHANIC
from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_COVERAGE


def fixed_typed_result_requires_current_ability(binding, template, required):
    from ..entry_designations import LINKED_PLAYER_CAST_MILL_TEMPLATE, LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE
    from ..power_sacrifice_model import (
        ATTACK_SACRIFICE_STAT_TEMPLATE, CONTROLLER_SACRIFICE_LIFE_TEMPLATE, ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE,
    )
    from .fixed_source_combat_growth import FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS

    return required and (
        template in FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS
        or binding.variant == "fixed_entry_return_requirement"
        or template in {
            "scheduled-player-hand-difference-v1", "linked-entry-opponent-upkeep-damage-v1",
            "fixed-counter-upkeep-target-hand-life-v1", "upkeep-fixed-other-controlled-creature-damage-v1",
            CONTROLLER_SACRIFICE_LIFE_TEMPLATE, ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE, ATTACK_SACRIFICE_STAT_TEMPLATE,
            LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE, LINKED_PLAYER_CAST_MILL_TEMPLATE,
        }
    )


def trigger_runtime_coverage(binding, *, current_ability, qualified_zone_variant, aggregated_zone_variant, ability_word_public_event_variants, public_event_variants, counter_event_variants, one_or_more_variants, multi_event_variants, effect_mechanics=()) -> tuple[str, ...]:
    values: list[str] = []
    from ..rules.trigger_source_information import UNTAPPED_DAMAGE_DRAW_COVERAGE, UNTAPPED_DAMAGE_DRAW_VARIANT

    if binding.variant == UNTAPPED_DAMAGE_DRAW_VARIANT:
        values.append(UNTAPPED_DAMAGE_DRAW_COVERAGE)
    if (binding.event.value == FIRST_LIFE_GAIN_EVENT or PREPARATION_RESULT_MECHANIC in effect_mechanics
            or binding.variant == "source_graveyard_arrival_shuffle"):
        current_ability = True
    from ..additional_beginning_model import ADDITIONAL_BEGINNING_VARIANTS

    if binding.variant in ADDITIONAL_BEGINNING_VARIANTS:
        current_ability = True
    if current_ability or binding.variant in {qualified_zone_variant, aggregated_zone_variant} or binding.variant in {'your first main phase', 'your precombat main phase', 'your second main phase', 'your postcombat main phase', *ability_word_public_event_variants, *public_event_variants - counter_event_variants, 'class_level_changed'}:
        values.append(CURRENT_ABILITY_FRAGMENT_COVERAGE)
    if binding.variant in one_or_more_variants:
        values.append('one_or_more_event_batch')
    if binding.variant in multi_event_variants:
        values.append('fixed_multi_event_subscription')
    if getattr(getattr(binding, "spell_subject", None), "requires_no_mana_spent", False):
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition"))
    if EVENT_SPELL_COPY_MECHANIC in effect_mechanics and is_other_spell_self_cast_condition(binding.event_condition):
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition", OTHER_SPELL_CAST_MECHANIC))
    if is_combat_damage_entry_condition(binding.event_condition):
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition", COMBAT_DAMAGE_ENTRY_COVERAGE))
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
