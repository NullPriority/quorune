from __future__ import annotations

"""Exact capability ownership for current-hand fixed putbacks after drawing."""

from ..fixed_hand_putback_model import (
    FIXED_HAND_PUTBACK_OPERATION as OP, FIXED_HAND_PUTBACK_CAPABILITY as CAP,
    FIXED_HAND_PUTBACK_MECHANIC as MECHANIC, PutbackPlacement as Placement,
    decode_fixed_hand_putback,
)

def fixed_hand_putback_dependencies(effect):
    spec=decode_fixed_hand_putback(effect)
    return (CAP,'zone.change.destination_replacement',
            *(('library.shuffle.fixed_controller',) if spec.placement is Placement.SHUFFLE else ()))


def fixed_putback_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if target_schema is not None or MECHANIC not in mechanic_ids:return ()
    if len(effects)==1:
        try:return fixed_hand_putback_dependencies(effects[0])
        except (ValueError,TypeError):return ()
    if len(effects)!=2:return ()
    draw,putback=effects
    try:
        spec=decode_fixed_hand_putback(putback)
        required=fixed_hand_putback_dependencies(putback)
    except (ValueError,TypeError):return ()
    if (set(draw)!={'op','player','count','private'} or draw['op']!='draw'
            or draw['player']!='$controller' or draw['private'] is not True
            or type(draw['count']) is not int
            or (draw['count'],spec.count,spec.placement.value) not in {
                (2,2,'bottom'),(3,1,'bottom'),(2,1,'shuffle'),(3,2,'either')}):
        return ()
    return (*required,'zone.draw.library_to_hand')


def fixed_putback_payload_dependencies(value):
    return fixed_hand_putback_dependencies(value) if value.get('op')==OP else ()


def is_closed_fixed_putback_program(program):
    required=fixed_putback_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
