"""Closed alternative-count discard and optional preceding draw shapes."""
from collections.abc import Mapping
from ..alternative_discard_model import (AlternativeDiscardCountSpec,ALTERNATIVE_DISCARD_CAPABILITY,
    ALTERNATIVE_DISCARD_MECHANIC,alternative_discard_count)
from .affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
from .node_capability_shapes import fixed_draw_node_capabilities


def alternative_discard_node_capabilities(*,effects,target_schema,mechanic_ids):
    mechanics=set(mechanic_ids)
    if ALTERNATIVE_DISCARD_MECHANIC not in mechanics or len(effects) not in {1,2}:return ()
    discard=effects[-1]
    if not isinstance(discard,Mapping) or not alternative_discard_count(discard.get('count')):return ()
    try:spec=AlternativeDiscardCountSpec.from_dict(discard['count'])
    except (TypeError,ValueError):return ()
    required=fixed_affected_player_discard_node_capabilities(effects=({**discard,'count':spec.fallback_count},),
        target_schema=target_schema,mechanic_ids=mechanics,allow_controller=True)
    if not required:return ()
    if len(effects)==2:
        draw=effects[0]
        draw_required=fixed_draw_node_capabilities(effects=(draw,),target_schema=target_schema,mechanic_ids=mechanics)
        if (not draw_required or draw.get('op')!='draw' or discard['players']!=[draw.get('player')]):return ()
        required=(*required,*draw_required)
    return tuple(sorted({*required,ALTERNATIVE_DISCARD_CAPABILITY}))


def is_closed_alternative_discard_program(program):
    required=alternative_discard_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(required and set(required)<=set(program.capability_dependencies))
