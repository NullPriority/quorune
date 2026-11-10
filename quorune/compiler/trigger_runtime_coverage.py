"""Subscription-derived runtime coverage for generated trigger programs."""
from __future__ import annotations

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..attack_batch_model import DECLARED_ATTACK_POWER_COVERAGE
from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
from ..life_change import FIRST_LIFE_GAIN_EVENT
from ..preparation_model import PREPARATION_RESULT_MECHANIC
from ..rules.attacked_player_life import ATTACKED_PLAYER_LIFE_COVERAGE
from ..rules.transform_capability_shapes import TRIGGERED_SOURCE_TRANSFORM_MECHANIC
from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_COVERAGE


def trigger_runtime_coverage(binding, *, current_ability, qualified_zone_variant, aggregated_zone_variant, ability_word_public_event_variants, public_event_variants, counter_event_variants, one_or_more_variants, multi_event_variants, effect_mechanics=()) -> tuple[str, ...]:
    values: list[str] = []
    if binding.event.value == FIRST_LIFE_GAIN_EVENT or PREPARATION_RESULT_MECHANIC in effect_mechanics:
        current_ability = True
    if current_ability or binding.variant in {qualified_zone_variant, aggregated_zone_variant} or binding.variant in {'your first main phase', 'your precombat main phase', 'your second main phase', 'your postcombat main phase', *ability_word_public_event_variants, *public_event_variants - counter_event_variants, 'class_level_changed'}:
        values.append(CURRENT_ABILITY_FRAGMENT_COVERAGE)
    if binding.variant in one_or_more_variants:
        values.append('one_or_more_event_batch')
    if binding.variant in multi_event_variants:
        values.append('fixed_multi_event_subscription')
    if binding.public_state_condition is not None:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, 'intervening_condition', FIXED_PUBLIC_STATE_INTERVENING_COVERAGE))
    if binding.attacked_player_life:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, 'intervening_condition', ATTACKED_PLAYER_LIFE_COVERAGE))
    if binding.minimum_declared_attack_power is not None:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, 'intervening_condition', DECLARED_ATTACK_POWER_COVERAGE))
    if TRIGGERED_SOURCE_TRANSFORM_MECHANIC in effect_mechanics:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, SPELL_HISTORY_TRANSFORM_COVERAGE))
    return tuple(dict.fromkeys(values))
