from __future__ import annotations

"""Independent fixed set results crossing normalized public-event bindings."""

from typing import Mapping
from .counter_placement_capability_shapes import fixed_counter_placement_set_node_capabilities

PUBLIC_SET_EVENT_MECHANIC='fixed-public-event-set-counter'
PUBLIC_SET_EVENT_CAPABILITY='counter.producer.fixed_public_set_event'


def fixed_public_event_set_result_capabilities(*,effects,target_schema,mechanic_ids):
    mechanics=set(mechanic_ids)-{PUBLIC_SET_EVENT_MECHANIC}
    if target_schema is not None or len(effects)!=1 or not isinstance(effects[0],Mapping):return ()
    effect=effects[0]
    direct=fixed_counter_placement_set_node_capabilities(effects=effects,target_schema=None,mechanic_ids=mechanics)
    if direct:return direct
    from ..resolution_conditions import RESOLUTION_CONDITION_OPERATION,validate_resolution_condition_instruction
    if effect.get('op')!=RESOLUTION_CONDITION_OPERATION:return ()
    try:_condition,conditional=validate_resolution_condition_instruction(effect)
    except (ValueError,TypeError,KeyError):return ()
    if effect.get('prefix_mechanic_ids'):return ()
    branches=((conditional,effect['mechanic_ids']),)
    if effect.get('schema_version')==3:branches+=((effect['otherwise_effects'],effect['otherwise_mechanic_ids']),)
    if any(not fixed_counter_placement_set_node_capabilities(effects=branch,target_schema=None,mechanic_ids=ids) for branch,ids in branches):return ()
    from .resolution_condition_shapes import resolution_condition_node_capabilities
    return resolution_condition_node_capabilities(effects=effects,target_schema=None,mechanic_ids=mechanics)


def public_event_counter_set_node_capabilities(*,effects,target_schema,mechanic_ids,**_unused):
    if PUBLIC_SET_EVENT_MECHANIC not in mechanic_ids:return ()
    required=fixed_public_event_set_result_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    return (PUBLIC_SET_EVENT_CAPABILITY,*required) if required else ()
