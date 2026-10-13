from __future__ import annotations


def special_node_capabilities(*,effects,target_schema,mechanics,cost_schema=None):
    """Route independently owned special shapes before ordinary leaf composition."""
    if 'transmute' in mechanics:
        from ..transmute import transmute_node_capabilities
        return transmute_node_capabilities(effects=effects, target_schema=target_schema,
            mechanic_ids=mechanics, cost_schema=cost_schema)
    from .temporary_ability_grant import TEMPORARY_QUOTED_GRANT_MECHANIC, temporary_quoted_grant_node_capabilities
    if TEMPORARY_QUOTED_GRANT_MECHANIC in mechanics:
        return temporary_quoted_grant_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics, cost_schema=cost_schema)
    if 'backup' in mechanics:
        from .temporary_ability_grant import backup_node_capabilities
        return backup_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics,cost_schema=cost_schema)
    if 'extort' in mechanics:
        from .extort import extort_node_capabilities
        return extort_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics,cost_schema=cost_schema)
    if 'public-quantity-mana' in mechanics:
        from .public_quantity_mana_shapes import public_quantity_mana_node_capabilities
        return public_quantity_mana_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC,RESOLUTION_CONDITION_OPERATION
    from .effect_shape_walkers import nested_effect_operations
    if RESOLUTION_CONDITION_MECHANIC in mechanics or RESOLUTION_CONDITION_OPERATION in nested_effect_operations(effects):
        from .resolution_condition_shapes import resolution_condition_node_capabilities
        return resolution_condition_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics,cost_schema=cost_schema)
    return None
