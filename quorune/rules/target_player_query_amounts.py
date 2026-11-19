"""Independent target/query/result ownership for a target-player public quantity."""
from collections.abc import Mapping
from ..target_player_query_amount_model import (
    TargetPlayerQueryAmountSpec,TARGET_PLAYER_QUERY_AMOUNT_KIND as KIND,
    TARGET_PLAYER_QUERY_AMOUNT_CAPABILITY as CAP,
)
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY
from ..characteristic_fragments import characteristic_quantity_capabilities
from ..replacement.immutable import thaw_value


def target_player_query_value_dependencies(value):
    if not isinstance(value,Mapping) or value.get('kind')!=KIND:return ()
    spec=TargetPlayerQueryAmountSpec.from_dict(value)
    return tuple(sorted({CAP,PUBLIC_QUERY_AMOUNT_CAPABILITY,*characteristic_quantity_capabilities(spec.amount.quantity)}))


def _player_schema_is_closed(schema):
    raw=thaw_value(schema)
    return isinstance(raw,Mapping) and type(raw.get('count')) is int and any(raw=={
        'zones':['player'],'categories':['player'],'player_relation':relation,'count':1}
        for relation in ('any','opponent'))


def target_player_query_node_capabilities(*,effects,target_schema,mechanic_ids=(),**kwargs):
    if len(effects)!=1 or not _player_schema_is_closed(target_schema):return ()
    effect=effects[0];op=effect.get('op');slot={'draw':'count','life':'delta'}.get(op)
    value=effect.get(slot) if slot else None
    if not isinstance(value,Mapping) or value.get('kind')!=KIND:return ()
    try:
        spec=TargetPlayerQueryAmountSpec.from_dict(value);projected={**effect,slot:2*spec.amount.coefficient}
        recipient=effect.get('player')
        if recipient not in ('$controller','$target.0'):return ()
        frame=target_schema if recipient=='$target.0' else None
        if op=='draw':
            from .node_capability_shapes import fixed_draw_node_capabilities
            base=fixed_draw_node_capabilities(effects=(projected,),target_schema=frame,
                mechanic_ids=('cr-121-drawing-a-card','cr-115-targets'))
        else:
            from .fixed_controller_effect_shapes import fixed_life_node_capabilities
            base=fixed_life_node_capabilities(effects=(projected,),target_schema=frame,
                mechanic_ids=('cr-119-life','cr-115-targets'))
        if not base:return ()
        return tuple(sorted({*target_player_query_value_dependencies(value),'target.revalidate_resolution',*base}))
    except (ValueError,TypeError,KeyError):return ()


def target_player_query_program_dependencies(program):
    found=[effect for effect in program.effects if isinstance(effect,Mapping)
        and any(isinstance(value,Mapping) and value.get('kind')==KIND for value in effect.values())]
    if not found:return ()
    if not ((program.event=='resolve' and program.active_zone=='stack' and str(program.ability_id).startswith('spell:'))
            or (program.event=='permanent.enter.self' and program.active_zone=='battlefield')):
        raise ValueError('Target public query requires its represented spell or self-entry result frame')
    required=target_player_query_node_capabilities(effects=program.effects,target_schema=program.target_schema)
    if not required:raise ValueError('Target public query requires one player target and a complete ordinary result')
    return required


def is_closed_target_player_query_program(program):
    try:required=target_player_query_program_dependencies(program)
    except (ValueError,TypeError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)
