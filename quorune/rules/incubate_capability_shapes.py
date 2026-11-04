from __future__ import annotations

from ..incubate_model import (INCUBATE_OPERATION,INCUBATE_MECHANIC,INCUBATE_DEPENDENCIES,
    validate_incubate_effect)


def incubate_node_capabilities(*,effects,target_schema,mechanic_ids):
    if INCUBATE_MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:return ()
    effect = effects[0]
    try:validate_incubate_effect(effect)
    except (TypeError,ValueError,KeyError):return ()
    if effect['op'] != INCUBATE_OPERATION or effect['controller'] != '$controller' or effect['source'] != '$source':return ()
    return INCUBATE_DEPENDENCIES


def is_closed_incubate_program(program):
    required = incubate_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)
