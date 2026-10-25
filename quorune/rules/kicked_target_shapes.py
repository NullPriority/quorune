from __future__ import annotations

"""Independent closure of fixed prefix and conditional Kicker target roles."""

from ..kicked_target_model import (KICKED_TARGET_CAPABILITY,validate_kicked_target_instruction,
    bound_kicked_target_schema,scope_target_role,target_roles)
from ..replacement.immutable import thaw_value


def kicked_target_capabilities(*,effects,target_schema,mechanic_ids,cost_schema=None):
    from .capabilities import capability_dependencies_for_node
    if not effects or effects[-1].get('schema_version')!=10:return ()
    wrapper=effects[-1]
    try:_,children=validate_kicked_target_instruction(wrapper)
    except (TypeError,ValueError,KeyError):return ()
    binding=wrapper['target_binding']
    from ..targets import TargetGroup
    try:
        for group in binding['groups']:TargetGroup.from_mapping(group['schema'],default_id=group['id'])
    except (TypeError,ValueError):return ()
    if wrapper['player']!='$controller' or wrapper['cast_fact']!='$context.kicked' or thaw_value(target_schema)!=bound_kicked_target_schema(binding,True):return ()
    mechanics=set(mechanic_ids)
    if not {'fixed-resolution-public-condition','fixed-cast-fact-condition'}<=mechanics:return ()
    groups={g['id']:g for g in binding['groups']};prefix=effects[:-1]
    if bool(prefix)!=bool(wrapper['prefix_mechanic_ids']) or not set((*wrapper['mechanic_ids'],*wrapper['prefix_mechanic_ids']))<=mechanics:return ()
    if target_roles(prefix) and ('target_0' not in groups or target_roles(prefix)!={'$target_group.target_0.0'}):return ()
    requirements={KICKED_TARGET_CAPABILITY,'resolution.effect.public_condition','resolution.effect.fixed_cast_fact'}
    for branch,role,ids in [(prefix,'target_0',wrapper['prefix_mechanic_ids']),(children,'target_1',wrapper['mechanic_ids'])]:
        if not branch:continue
        targeted=bool(target_roles(branch));schema=groups[role]['schema'] if targeted and role in groups else None
        restored=scope_target_role(branch,role,restore=True) if targeted else thaw_value(branch)
        dependencies=capability_dependencies_for_node(effects=restored,target_schema=schema,mechanic_ids=ids,cost_schema=cost_schema)
        if not dependencies:return ()
        requirements.update(dependencies)
    return tuple(sorted(requirements))
