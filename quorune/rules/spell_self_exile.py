from __future__ import annotations

"""Terminal resolving-spell self exile over an independently closed body."""

CAPABILITY = 'zone.exile.resolving_spell'
MECHANIC = 'spell-resolution-self-exile'
TEMPLATE = 'terminal-spell-self-exile-v1'


def spell_self_exile_node_capabilities(*, effects, target_schema, mechanics, cost_schema=None):
    if MECHANIC not in mechanics or not effects:
        return ()
    from .capabilities import capability_dependencies_for_node
    body = capability_dependencies_for_node(effects=effects,target_schema=target_schema,
        mechanic_ids=set(mechanics)-{MECHANIC},cost_schema=cost_schema)
    return tuple(sorted({CAPABILITY,*body})) if body else ()


def spell_self_exile_runtime_dependencies(program):
    marked = MECHANIC in program.coverage
    wrapped = TEMPLATE in str(program.provenance.get('template_id') or '').split('+')
    if not marked and not wrapped:
        return ()
    if (not marked or not wrapped or program.destination != 'exile'
            or getattr(program,'event',None) != 'resolve'
            or getattr(program,'active_zone',None) != 'stack'
            or not str(getattr(program,'ability_id','')).startswith('spell:')):
        raise ValueError('Terminal self exile requires its complete resolving-spell context')
    return (CAPABILITY,)
