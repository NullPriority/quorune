from __future__ import annotations

from .blight_model import BLIGHT_CAPABILITY, BLIGHT_MECHANIC, fixed_blight_amount


def fixed_blight_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or BLIGHT_MECHANIC not in mechanic_ids:
        return ()
    try:
        fixed_blight_amount(effects[0])
    except ValueError:
        return ()
    return (BLIGHT_CAPABILITY,)


def is_closed_fixed_blight_program(program):
    required = fixed_blight_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)
