from __future__ import annotations

from .endure_model import ENDURE_CAPABILITY, ENDURE_MECHANIC, fixed_endure_amount


def fixed_endure_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or ENDURE_MECHANIC not in mechanic_ids:
        return ()
    try:
        fixed_endure_amount(effects[0])
    except ValueError:
        return ()
    return (ENDURE_CAPABILITY,)


def is_closed_fixed_endure_program(program):
    required = fixed_endure_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)
