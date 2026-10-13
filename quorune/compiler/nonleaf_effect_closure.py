from __future__ import annotations

"""Closed special effect shapes sharing generated-program promotion."""


def nonleaf_effect_program_is_closed(program):
    from ..rules.linked_exile_return_shapes import linked_exile_return_node_capabilities
    from ..rules.resolution_condition_shapes import resolution_condition_node_capabilities
    from ..rules.affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
    for resolver in (resolution_condition_node_capabilities,linked_exile_return_node_capabilities,
                     fixed_affected_player_sacrifice_node_capabilities):
        required=resolver(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
        if required and set(required).issubset(program.capability_dependencies):return True
    return False
