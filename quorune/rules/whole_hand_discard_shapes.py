from __future__ import annotations

"""Strict whole-hand discard capability and program composition shapes."""

from ..whole_hand_discard_model import WHOLE_HAND_DISCARD_CAPABILITY,WHOLE_HAND_DISCARD_OPERATION,WHOLE_HAND_DISCARD_MECHANIC


def whole_hand_discard_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    del kwargs
    if len(effects)!=1 or WHOLE_HAND_DISCARD_MECHANIC not in mechanic_ids:
        return ()
    effect=effects[0]
    if set(effect)!={'op','actor','players'} or effect['op']!=WHOLE_HAND_DISCARD_OPERATION or effect['actor']!='$controller':
        return ()
    raw=effect['players'];deps={WHOLE_HAND_DISCARD_CAPABILITY}
    if raw==['$target.0']:
        if target_schema not in ({'zones':['player'],'categories':['player'],'player_relation':'any','count':1},
            {'zones':['player'],'categories':['player'],'player_relation':'opponent','count':1}):return ()
        deps.add('target.revalidate_resolution')
    elif target_schema is not None or raw not in ('all','opponents',['$controller']):return ()
    return tuple(sorted(deps))


def is_closed_whole_hand_discard_program(program):
    deps=whole_hand_discard_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    if not deps and program.provenance.get('template_id')=='whole-hand-discard-public-draw-sequence-v1':
        from ..query_effect_amount_model import PublicQueryAmountSpec,PUBLIC_QUERY_AMOUNT_KIND
        from ..scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_KIND
        from .closed_effect_program_shapes import closed_effect_program_node_capabilities
        from copy import deepcopy
        if len(program.effects)!=2 or program.effects[0].get('op')!=WHOLE_HAND_DISCARD_OPERATION or program.effects[1].get('op')!='draw':return False
        value=program.effects[1].get('count')
        if not isinstance(value,dict):return False
        try:
            if value.get('kind')==PUBLIC_QUERY_AMOUNT_KIND:
                spec=PublicQueryAmountSpec.from_dict(value)
                producer='quantity_expression.public_query_effect_amount';marker='public-query-effect-amount'
            elif value.get('kind')==SCALAR_AMOUNT_KIND:
                spec=ScalarEffectAmountSpec.from_dict(value)
                producer='quantity_expression.scalar_effect_amount';marker='scalar-effect-amount'
            else:return False
        except (TypeError,ValueError):return False
        if spec.coefficient!=1 or marker not in program.coverage or producer not in program.capability_dependencies:return False
        effects=deepcopy(program.effects);effects[1]['count']=2
        mechanics=set(program.coverage)-{'public-query-effect-amount','scalar-effect-amount','declared-effect-amount'}
        deps=closed_effect_program_node_capabilities(effects=effects,target_schema=program.target_schema,mechanic_ids=mechanics)
    return bool(deps) and set(deps)<=set(program.capability_dependencies)
