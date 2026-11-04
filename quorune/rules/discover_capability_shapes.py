from __future__ import annotations

from ..discover_model import DISCOVER_CAPABILITY,validate_discover_effect


def discover_node_capabilities(*,effects,target_schema,mechanic_ids):
    if 'discover' not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    try:validate_discover_effect(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    return (DISCOVER_CAPABILITY,)


def is_closed_discover_program(program):
    required=discover_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
