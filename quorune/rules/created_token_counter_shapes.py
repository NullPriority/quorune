from __future__ import annotations

from ..created_token_counter_model import CREATED_TOKEN_COUNTER_MECHANIC,DEPENDENCIES,validate_created_token_counter


def created_token_counter_node_capabilities(*,effects,target_schema,mechanic_ids):
    if CREATED_TOKEN_COUNTER_MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    try:validate_created_token_counter(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    if effects[0]['controller']!='$controller' or effects[0]['source']!='$source':return ()
    return DEPENDENCIES


def is_closed_created_token_counter_program(program):
    required=created_token_counter_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
