from __future__ import annotations

"""Independent branch and target closure for Kicker target replacement."""

from ..kicked_target_replacement_model import (KICKED_TARGET_REPLACEMENT_CAPABILITY,
    validate_kicked_target_replacement_instruction)
from ..replacement.immutable import thaw_value
from ..targets import TargetGroup


def kicked_target_replacement_capabilities(*,effects,target_schema,mechanic_ids,cost_schema=None):
    from .capabilities import capability_dependencies_for_node
    if len(effects)!=1:return ()
    wrapper=effects[0]
    try:
        validate_kicked_target_replacement_instruction(wrapper)
        binding=wrapper['target_binding']
        for name in ('normal','kicked'):TargetGroup.from_mapping(binding[name],default_id='target_0')
    except (TypeError,ValueError,KeyError):return ()
    if (wrapper['player']!='$controller' or wrapper['cast_fact']!='$context.kicked'
            or thaw_value(target_schema)!=thaw_value(binding['kicked'])):return ()
    mechanics=set(mechanic_ids)
    if not {'fixed-resolution-public-condition','fixed-cast-fact-condition'}<=mechanics:return ()
    if not set((*wrapper['mechanic_ids'],*wrapper['otherwise_mechanic_ids']))<=mechanics:return ()
    required={KICKED_TARGET_REPLACEMENT_CAPABILITY,'resolution.effect.public_condition','resolution.effect.fixed_cast_fact'}
    for values,ids,schema in [(wrapper['effects'],wrapper['mechanic_ids'],binding['kicked']),
            (wrapper['otherwise_effects'],wrapper['otherwise_mechanic_ids'],binding['normal'])]:
        deps=capability_dependencies_for_node(effects=values,target_schema=schema,mechanic_ids=ids,cost_schema=cost_schema)
        if not deps:return ()
        required.update(deps)
    return tuple(sorted(required))
