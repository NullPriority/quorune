from __future__ import annotations

"""Descriptor-owned runtime requirements beyond registered operation minima."""

from ..object_predicate import object_query_capabilities
from ..rules.casting_additional_cost_groups import announced_additional_cost_dependencies
from ..rules.fixed_homogeneous_target_set_capability_shapes import announced_target_runtime_dependencies
from ..rules.resolution_condition_shapes import same_target_self_replacement_runtime_dependencies


def program_dynamic_runtime_requirements(program):
    required=set();blockers=set()
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
    return required,blockers
