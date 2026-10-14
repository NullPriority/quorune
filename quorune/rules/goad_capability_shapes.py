from __future__ import annotations

from ..goad_instruction import validate_fixed_goad,GOAD_CAPABILITY,GOAD_MECHANIC
from ..compiler.direct_target import DirectPermanentTargetSpec
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed,direct_target_predicate_capabilities


def fixed_goad_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    del kwargs
    if not {GOAD_MECHANIC,'goad','bound-effect-program'} & set(mechanic_ids) or len(effects)!=1:return ()
    effect=effects[0]
    legacy=set(effect)=={'op','card'} and effect.get('op')=='goad'
    try:query=None if legacy else validate_fixed_goad(effect)
    except (ValueError,TypeError):return ()
    if query is not None:
        if query.controller=='$target.0':
            if target_schema!={'zones':['player'],'categories':['player'],'player_relation':'any','count':1}:return ()
            return (GOAD_CAPABILITY,'target.revalidate_resolution')
        if target_schema is not None:return ()
        if query.controller not in {None,'$controller'} or query.excluded_controllers not in {(),('$controller',)} or query.exclude_ref not in {None,'$source'}:return ()
        return (GOAD_CAPABILITY,)
    if effect['card']!='$target.0' or not direct_permanent_target_schema_is_closed(target_schema):return ()
    target=DirectPermanentTargetSpec.from_target_schema(target_schema)
    if 'creature' not in target.types_all and target.types_any!=('creature',):return ()
    return tuple(sorted({GOAD_CAPABILITY,'target.revalidate_resolution',*direct_target_predicate_capabilities(target_schema)}))


def is_closed_fixed_goad_program(program):
    required=fixed_goad_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
