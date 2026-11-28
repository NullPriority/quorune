"""Shape closure and payload-derived minimums for all-owner control effects."""
from collections.abc import Mapping
from ..owner_control_model import (OWNER_CONTROL_CAPABILITY,OWNER_CONTROL_MECHANIC,
    OWNER_CONTROL_OPERATION,validate_owner_control_effect)


def owner_control_node_capabilities(*,effects,target_schema,mechanic_ids):
    if (len(effects)!=1 or target_schema is not None
            or not {OWNER_CONTROL_MECHANIC,'cr-611-continuous-effects'}<=set(mechanic_ids)):return ()
    try:validate_owner_control_effect(effects[0])
    except (TypeError,ValueError):return ()
    return (OWNER_CONTROL_CAPABILITY,'continuous.control.fixed_resolution') if effects[0]['source']=='$source' else ()


def owner_control_runtime_dependencies(program):
    from .directional_control import directional_control_runtime_dependencies

    required=set(directional_control_runtime_dependencies(program))
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('op')==OWNER_CONTROL_OPERATION:
                validate_owner_control_effect(value)
                if value['source']!='$source':raise ValueError('Owner control lost its original source')
                required.add(OWNER_CONTROL_CAPABILITY)
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))
