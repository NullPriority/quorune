"""Close the modal body and its printed Pawprint budget independently."""

from collections.abc import Mapping

from ..pawprint_modes import PAWPRINT_CAPABILITY, PAWPRINT_FIELD, PAWPRINT_MECHANIC, PawprintModeBudget


def pawprint_capabilities(*, effects, target_schema, mechanics, cost_schema, modal_resolver):
    if (not isinstance(cost_schema, Mapping) or set(cost_schema) != {PAWPRINT_FIELD}
            or effects or not {PAWPRINT_MECHANIC, "fixed-nonrepeating-modal"} <= set(mechanics)):
        return ()
    try:
        PawprintModeBudget.from_dict(cost_schema[PAWPRINT_FIELD]).validate_schema(target_schema)
    except (TypeError, ValueError):
        return ()
    owned = modal_resolver(
        effects=effects, target_schema=target_schema,
        mechanics=tuple(mechanic for mechanic in mechanics if mechanic != PAWPRINT_MECHANIC),
        allow_pawprint_draw=True,
    )
    return (*owned, PAWPRINT_CAPABILITY) if owned else ()


def pawprint_runtime_dependencies(program):
    schema = program.cost_schema or {}
    if (PAWPRINT_FIELD not in schema and PAWPRINT_CAPABILITY not in program.capability_dependencies
            and PAWPRINT_MECHANIC not in program.coverage):
        return ()
    if (set(schema) != {PAWPRINT_FIELD} or PAWPRINT_MECHANIC not in program.coverage
            or program.effects or program.handlers
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.event_condition is not None):
        raise ValueError("Pawprint selection requires its complete resolving spell")
    PawprintModeBudget.from_dict(schema[PAWPRINT_FIELD]).validate_schema(program.target_schema)
    return (PAWPRINT_CAPABILITY,)
