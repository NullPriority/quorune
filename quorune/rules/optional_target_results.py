"""Validate and guard the optional target before querying dependent information."""
from collections.abc import Mapping
from ..optional_target_result_model import (
    OPTIONAL_TARGET_RESULT_FIELD,OPTIONAL_TARGET_RESULT_CAPABILITY,OPTIONAL_TARGET_RESULT_MECHANIC,
    OptionalTargetResultSpec,
)
from ..target_controller_reference import controller_reference_index
from ..scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin


def optional_target_result_payload_dependencies(effect):
    if OPTIONAL_TARGET_RESULT_FIELD not in effect:return ()
    OptionalTargetResultSpec.from_dict(effect[OPTIONAL_TARGET_RESULT_FIELD])
    if (set(effect)!={'op','player','delta',OPTIONAL_TARGET_RESULT_FIELD} or effect['op']!='life'
            or controller_reference_index(effect['player'])!=0):
        raise ValueError('Optional result requires the original target controller life instruction')
    amount=ScalarEffectAmountSpec.from_dict(effect['delta'])
    if (amount.schema_version!=1 or amount.origin is not ScalarAmountOrigin.TARGET
            or amount.characteristic!='power' or amount.coefficient!=1 or amount.binding_id is not None):
        raise ValueError('Optional result requires the original target power')
    return (OPTIONAL_TARGET_RESULT_CAPABILITY,'resolution.target_controller_information',
        'quantity_expression.scalar_effect_amount')


def bind_optional_target_result(effect, targets):
    """Return None before any lookup for an unannounced optional target."""
    if OPTIONAL_TARGET_RESULT_FIELD not in effect:return effect
    optional_target_result_payload_dependencies(effect)
    spec=OptionalTargetResultSpec.from_dict(effect[OPTIONAL_TARGET_RESULT_FIELD])
    if spec.target_is_absent(targets):return None
    return {k:v for k,v in effect.items() if k!=OPTIONAL_TARGET_RESULT_FIELD}


def optional_target_result_node_capabilities(*,effects,target_schema,mechanic_ids):
    if (len(effects)!=2 or OPTIONAL_TARGET_RESULT_MECHANIC not in mechanic_ids
            or not isinstance(target_schema,Mapping)):return ()
    prefix,result=effects
    if (prefix!={'op':'exile_permanent_targets','cards':'$targets','maximum_targets':1}
            or type(prefix.get('maximum_targets')) is not int):return ()
    required={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'up_to':1}
    schema=dict(target_schema)
    if 'source_exclusion' in schema:
        if schema.pop('source_exclusion') is not True:return ()
    if schema!=required or type(schema['up_to']) is not int:return ()
    try:deps=optional_target_result_payload_dependencies(result)
    except (TypeError,ValueError):return ()
    if not deps:return ()
    from .capabilities import capability_dependencies_for_node
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities
    from ..compiler.fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC
    mandatory={k:v for k,v in target_schema.items() if k!='up_to'};mandatory['count']=1
    mechanics=tuple(m for m in mechanic_ids if m not in {OPTIONAL_TARGET_RESULT_MECHANIC,FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC})
    old=capability_dependencies_for_node(effects=({'op':'exile_permanent','card':'$target.0'},
        {k:v for k,v in result.items() if k!=OPTIONAL_TARGET_RESULT_FIELD}),target_schema=mandatory,mechanic_ids=mechanics)
    batch=fixed_homogeneous_target_set_node_capabilities(effects=(prefix,),target_schema=target_schema,mechanic_ids=mechanic_ids)
    return tuple(sorted({*deps,*old,*batch})) if old and batch else ()


def is_closed_optional_target_result_program(program):
    deps=optional_target_result_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(deps) and set(deps)<=set(program.capability_dependencies)


def optional_target_result_runtime_dependencies(program):
    deps=optional_target_result_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    if not deps:raise ValueError('Optional result requires its complete original target and ordered owners')
    return deps
