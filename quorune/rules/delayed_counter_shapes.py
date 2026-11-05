"""Deferred source counters retain their existing primitive capability closure."""
from ..delayed_object_model import DELAYED_OBJECT_CAPABILITY,DELAYED_OBJECT_MECHANIC,validate_delayed_object_instruction


def delayed_counter_node_capabilities(*,effects,target_schema,mechanic_ids):
    if DELAYED_OBJECT_MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    try:effect=validate_delayed_object_instruction(effects[0],compiled=True)
    except (ValueError,TypeError,KeyError):return ()
    if effect['schema_version']!=2:return ()
    from .node_capability_shapes import fixed_counter_placement_node_capabilities
    from .counter_removal_capabilities import fixed_counter_removal_node_capabilities
    primitive={key:effect[key] for key in ('counter','amount','source')}
    primitive.update(op=effect['action'],card=effect['object'])
    resolver=fixed_counter_placement_node_capabilities if effect['action']=='place_counters' else fixed_counter_removal_node_capabilities
    required=resolver(effects=(primitive,),target_schema=None,mechanic_ids=mechanic_ids)
    if not required:return ()
    return tuple(sorted({DELAYED_OBJECT_CAPABILITY,'trigger.placement.apnap',*required}))
