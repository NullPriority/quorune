from __future__ import annotations

"""Exact fixed single-object Connive descriptors; private stages never admit."""

from ..connive import CONNIVE_CAPABILITY, CONNIVE_MECHANIC, CONNIVE_OPERATION, FixedConniveSpec


def fixed_connive_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    del kwargs
    if CONNIVE_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    effect = effects[0]
    if set(effect) != {'op', 'card', 'player', 'spec'} or effect['op'] != CONNIVE_OPERATION:
        return ()
    try:
        FixedConniveSpec.from_dict(effect['spec'])
    except (ValueError, TypeError):
        return ()
    if effect['card'] == '$source' and effect['player'] == '$source.connive_controller' and target_schema is None:
        return (CONNIVE_CAPABILITY,)
    if effect['card'] != '$target.0' or effect['player'] != '$target.current_controller.0':
        return ()
    base = {'zones': ['battlefield'], 'categories': ['permanent'], 'types_any': ['creature'], 'count': 1}
    if target_schema not in (base, {**base, 'controller_relation': 'you'}):
        return ()
    return (CONNIVE_CAPABILITY, 'target.revalidate_resolution')


def is_closed_fixed_connive_program(program):
    required = fixed_connive_node_capabilities(effects=program.effects, target_schema=program.target_schema,
                                               mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)
