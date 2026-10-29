"""Keep both branches of a linked declaration independently capability-bound."""
from ..declared_cast_cost_model import DECLARED_CAST_COST_CAPABILITY, DECLARED_CAST_COST_MECHANIC
from ..declared_cost_conditions import validate_declared_cost_instruction
from ..resolution_conditions import RESOLUTION_CONDITION_CAPABILITY, RESOLUTION_CONDITION_MECHANIC


def declared_cost_condition_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    from .capabilities import capability_dependencies_for_node
    if len(effects) != 1 or not {DECLARED_CAST_COST_MECHANIC, RESOLUTION_CONDITION_MECHANIC} <= set(mechanic_ids):return ()
    wrapper = effects[0]
    try:validate_declared_cost_instruction(wrapper)
    except (ValueError, TypeError):return ()
    if wrapper['player'] != '$controller' or wrapper['cast_fact'] != '$context.declared_cast_costs':return ()
    if target_schema is not None and (target_schema.get('types_any') != ['creature'] or target_schema.get('count') != 1):return ()
    required = {DECLARED_CAST_COST_CAPABILITY, RESOLUTION_CONDITION_CAPABILITY}
    for branch, ids in ((wrapper['effects'], wrapper['mechanic_ids']), (wrapper['otherwise_effects'], wrapper['otherwise_mechanic_ids'])):
        if not set(ids) <= set(mechanic_ids):return ()
        if target_schema is not None:
            from .resolution_condition_shapes import _same_target_result_closed
            if not _same_target_result_closed(branch):return ()
        deps = capability_dependencies_for_node(effects=branch, target_schema=target_schema, mechanic_ids=ids, cost_schema=cost_schema)
        if not deps:return ()
        required.update(deps)
    return tuple(sorted(required))
