"""Capability requirements owned by spell and activation costs."""

from __future__ import annotations

from ..rules.public_value_costs import public_value_cost_dependencies
from ..rules.self_cost_numeric_counts import self_cost_numeric_count_dependencies
from ..rules.self_cost_public_counters import self_cost_public_counter_dependencies
from ..rules.self_cost_turn_counts import self_cost_turn_count_dependencies
from ..semantic_runtime.activated_abilities import activated_catalog_runtime_dependencies


def _cost_metric_runtime_requirements(program):
    from ..rules.announced_hand_discard import announced_hand_discard_activation_dependencies
    required, blockers = set(), set()
    from ..rules.gift_card_spells import gift_card_runtime_dependencies
    from ..rules.gift_fish_spells import gift_fish_runtime_dependencies
    from ..rules.gift_counter_spells import gift_counter_runtime_dependencies
    from ..rules.gift_graveyard_spells import runtime_dependencies as gift_graveyard_runtime_dependencies
    from ..rules.paid_sacrifice_copy import paid_sacrifice_copy_runtime_dependencies
    from ..semantic_runtime.mana_abilities import (
        exert_mana_runtime_dependencies, fixed_mana_life_runtime_dependencies,
        next_cast_library_mana_runtime_dependencies,
    )

    from ..rules.announced_subtype_sacrifice import announced_subtype_activation_runtime_dependencies
    for resolver, label in (
        (activated_catalog_runtime_dependencies, "runtime_ability_local_mana_reduction"),
        (announced_subtype_activation_runtime_dependencies, "runtime_announced_subtype_sacrifice"),
        (announced_hand_discard_activation_dependencies, "runtime_announced_hand_discard"),
        (fixed_mana_life_runtime_dependencies, "runtime_fixed_mana_life"),
        (next_cast_library_mana_runtime_dependencies, "runtime_next_cast_library_mana"),
        (exert_mana_runtime_dependencies, "runtime_exert_mana"),
        (paid_sacrifice_copy_runtime_dependencies, "runtime_paid_sacrifice_copy"),
        (gift_card_runtime_dependencies, "runtime_gift_card"),
        (gift_fish_runtime_dependencies, "runtime_gift_fish"),
        (gift_counter_runtime_dependencies, "runtime_gift_counter"),
        (gift_graveyard_runtime_dependencies, "runtime_gift_graveyard"),
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
