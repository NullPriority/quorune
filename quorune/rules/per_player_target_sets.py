"""Independent authority for per-player selection and controller retention."""
from collections.abc import Mapping
from ..compiler.per_player_target_sets import PER_PLAYER_TARGET_CAPABILITY,PER_PLAYER_TARGET_MECHANIC
from ..compiler.any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC
from ..compiler.fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC
from .any_number_target_shapes import any_number_target_node_capabilities
from .fixed_homogeneous_target_set_capability_shapes import _OPERATION_MECHANICS


def per_player_target_node_capabilities(*,effects,target_schema,mechanic_ids):
    if not isinstance(target_schema,Mapping):return ()
    if (target_schema.get('distinct_controllers') is not True or target_schema.get('retain_controller') is not True
            or target_schema.get('zones')!=['battlefield'] or target_schema.get('categories')!=['permanent']
            or target_schema.get('controller_relation','any') not in {'any','opponent'}):return ()
    schema={key:value for key,value in target_schema.items() if key not in {'distinct_controllers','retain_controller'}}
    owned=any_number_target_node_capabilities(effects=effects,target_schema=schema,
        mechanic_ids=set(mechanic_ids)-{PER_PLAYER_TARGET_MECHANIC})
    return tuple(sorted({PER_PLAYER_TARGET_CAPABILITY,*owned})) if owned else ()


def is_closed_per_player_target_program(program):
    required=per_player_target_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


def per_player_target_runtime_dependencies(program):
    schema=program.target_schema
    if not isinstance(schema,Mapping) or not {'distinct_controllers','retain_controller'}&set(schema):return ()
    inferred={ANY_NUMBER_TARGET_MECHANIC,FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,'cr-115-targets'}
    inferred.update(_OPERATION_MECHANICS[effect['op']] for effect in program.effects if effect.get('op') in _OPERATION_MECHANICS)
    required=per_player_target_node_capabilities(effects=program.effects,target_schema=schema,mechanic_ids=inferred)
    if not required:raise ValueError('Per-player targets lack their closed domain, cardinality or primitive')
    return required
