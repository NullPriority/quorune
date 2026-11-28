from __future__ import annotations

from ..created_token_counter_model import (
    ANNOUNCED_X_CREATE_AND_WIPE, CREATED_TOKEN_COUNTER_MECHANIC, CREATED_TOKEN_WIPE_MECHANIC, DESTROY_EXCEPT_CREATED,
    DEPENDENCIES, validate_announced_x_token_wipe, validate_created_token_counter,
)


def created_token_counter_node_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects) != 1:
        return ()
    if CREATED_TOKEN_WIPE_MECHANIC in mechanic_ids:
        try:
            return validate_announced_x_token_wipe(effects[0])
        except (TypeError, ValueError, KeyError):
            return ()
    if CREATED_TOKEN_COUNTER_MECHANIC not in mechanic_ids:
        return ()
    try:validate_created_token_counter(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    if effects[0]['controller']!='$controller' or effects[0]['source']!='$source':return ()
    return DEPENDENCIES


def is_closed_created_token_counter_program(program):
    required=created_token_counter_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    if required and program.effects[0].get('op') == ANNOUNCED_X_CREATE_AND_WIPE:
        try:
            validate_announced_x_token_wipe_program(program)
        except (ValueError, TypeError, KeyError):
            return False
    return bool(required) and set(required)<=set(program.capability_dependencies)


def validate_announced_x_token_wipe_program(program):
    if (len(program.effects) != 1 or program.target_schema is not None or program.handlers
            or program.event != 'resolve' or program.active_zone != 'stack'
            or not program.ability_id.startswith('spell:')):
        raise ValueError('Announced-X token destruction requires its original untargeted resolving spell')
    return validate_announced_x_token_wipe(program.effects[0])


def announced_x_token_wipe_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effect_operations

    operations = nested_effect_operations((program.effects, program.handlers))
    if DESTROY_EXCEPT_CREATED in operations:
        raise ValueError('Committed creation membership is not authored spell input')
    if ANNOUNCED_X_CREATE_AND_WIPE not in operations:
        return ()
    return validate_announced_x_token_wipe_program(program)
