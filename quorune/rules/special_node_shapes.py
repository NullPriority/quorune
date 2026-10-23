from __future__ import annotations


def special_node_capabilities(*,effects,target_schema,mechanics,cost_schema=None):
    """Route independently owned special shapes before ordinary leaf composition."""
    if 'any-number-target-set' in mechanics:
        from .any_number_target_shapes import any_number_target_node_capabilities
        return any_number_target_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if 'quantified-counter-target-set' in mechanics:
        from .counter_target_set_shapes import quantified_counter_target_capabilities
        return quantified_counter_target_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if 'spell-resolution-self-exile' in mechanics:
        from .spell_self_exile import spell_self_exile_node_capabilities
        return spell_self_exile_node_capabilities(effects=effects,target_schema=target_schema,
            mechanics=mechanics,cost_schema=cost_schema)
    if 'manifest' in mechanics and all(effect.get('op') == 'manifest_card' for effect in effects):
        from .manifest_shapes import manifest_node_capabilities
        return manifest_node_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics)
    if 'predefined-role-token' in mechanics:
        from .role_token_shapes import role_token_node_capabilities
        return role_token_node_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics)
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
    if not mechanics.intersection({'fixed-nonrepeating-modal', 'fixed-choose-one-modal-spell'}):
        from .fractional_player_result_shapes import half_life_node_capabilities, half_choice_node_capabilities
        if 'half-current-player-life-loss' in mechanics:
            return half_life_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
        if 'half-affected-player-domain-choice' in mechanics:
            return half_choice_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    return None
