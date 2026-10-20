from __future__ import annotations

"""Descriptor-owned runtime requirements beyond registered operation minima."""

from ..object_predicate import object_query_capabilities
from ..rules.casting_additional_cost_groups import announced_additional_cost_dependencies
from ..rules.fixed_homogeneous_target_set_capability_shapes import announced_target_runtime_dependencies
from ..rules.resolution_condition_shapes import same_target_self_replacement_runtime_dependencies
from ..rules.target_hand_count_shapes import target_hand_count_runtime_dependencies
from ..rules.temporary_keyword_removal import temporary_keyword_removal_runtime_dependencies
from ..rules.increment_condition import increment_runtime_dependencies
from ..targeted_spell_costs import self_target_reduction_runtime_dependencies
from ..rules.target_characteristic_sets import target_characteristic_set_runtime_dependencies
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
from ..rules.reconfigure import reconfigure_runtime_dependencies
from ..rules.paid_color_entry import paid_color_entry_runtime_dependencies


def program_dynamic_runtime_requirements(program):
    required=set();blockers=set()
    try:
        required.update(paid_color_entry_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_paid_color_entry:invalid:{exc}')
    try:
        required.update(reconfigure_runtime_dependencies(program))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_reconfigure:invalid:{exc}')
    try:
        required.update(self_cost_numeric_count_dependencies(program.handlers))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_self_cost_numeric_count:invalid:{exc}')
    try:
        required.update(self_cost_public_counter_dependencies(program.handlers))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_self_cost_public_counters:invalid:{exc}')
    try:
        required.update(self_cost_turn_count_dependencies(program.handlers))
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_self_cost_turn_count:invalid:{exc}')
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
        required.update(target_characteristic_set_runtime_dependencies(program))
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
    except (TypeError,ValueError) as exc:
        blockers.add(f'runtime_cast_cost:invalid:{exc}')
    try:
        required.update(same_target_self_replacement_runtime_dependencies(program))
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
