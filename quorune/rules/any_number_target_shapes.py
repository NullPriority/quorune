from __future__ import annotations

"""Cardinality authority projects results to their unchanged fixed-set owners."""

from collections.abc import Mapping
from copy import deepcopy

from ..compiler.any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC
from ..target_cardinality import (
    ANY_NUMBER_TARGET_CAPABILITY, AnnouncedTargetCardinality,
    bind_any_number_target_schema, validate_selected_target_count,
)


def any_number_target_node_capabilities(*, effects, target_schema, mechanic_ids):
    if ANY_NUMBER_TARGET_MECHANIC not in mechanic_ids or not isinstance(target_schema, Mapping):
        return ()
    try:
        spec = AnnouncedTargetCardinality.from_dict(target_schema["count_parameter"])
        if spec.parameter != "any_number" or len(effects) != 1:
            return ()
        effect = deepcopy(dict(effects[0]))
        validate_selected_target_count(effect["maximum_targets"])
        if effect.pop("announced_target_count") is not True:
            return ()
        effect["maximum_targets"] = 2
        schema = bind_any_number_target_schema(target_schema, 2)
    except (KeyError, TypeError, ValueError):
        return ()
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities
    from .target_characteristic_sets import target_characteristic_set_capabilities
    from .counter_target_set_shapes import quantified_counter_target_capabilities
    for owner in (fixed_homogeneous_target_set_node_capabilities, target_characteristic_set_capabilities, quantified_counter_target_capabilities):
        required = owner(effects=(effect,), target_schema=schema, mechanic_ids=mechanic_ids)
        if required:
            from .capabilities import capability_dependencies_for_node
            ambient=capability_dependencies_for_node(effects=(effect,),target_schema=schema,
                mechanic_ids=set(mechanic_ids)-{ANY_NUMBER_TARGET_MECHANIC})
            return tuple(sorted({*required, *ambient, ANY_NUMBER_TARGET_CAPABILITY}))
    return ()


def is_closed_any_number_target_program(program):
    required = any_number_target_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
