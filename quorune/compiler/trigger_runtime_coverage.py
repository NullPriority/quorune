"""Subscription-derived runtime coverage for generated trigger programs."""
from __future__ import annotations

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE


def trigger_runtime_coverage(binding, *, current_ability, qualified_zone_variant, aggregated_zone_variant, ability_word_public_event_variants, public_event_variants, counter_event_variants, one_or_more_variants, multi_event_variants) -> tuple[str, ...]:
    values: list[str] = []
    if current_ability or binding.variant in {qualified_zone_variant, aggregated_zone_variant} or binding.variant in {'your first main phase', 'your precombat main phase', 'your second main phase', 'your postcombat main phase', *ability_word_public_event_variants, *public_event_variants - counter_event_variants, 'class_level_changed'}:
        values.append(CURRENT_ABILITY_FRAGMENT_COVERAGE)
    if binding.variant in one_or_more_variants:
        values.append('one_or_more_event_batch')
    if binding.variant in multi_event_variants:
        values.append('fixed_multi_event_subscription')
    if binding.public_state_condition is not None:
        values.extend((CURRENT_ABILITY_FRAGMENT_COVERAGE, 'intervening_condition', FIXED_PUBLIC_STATE_INTERVENING_COVERAGE))
    return tuple(dict.fromkeys(values))
