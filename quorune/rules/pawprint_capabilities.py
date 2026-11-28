"""Close the modal body and its printed Pawprint budget independently."""

from collections.abc import Mapping

from ..pawprint_modes import (
    PAWPRINT_CAPABILITY, PAWPRINT_FIELD, PAWPRINT_MECHANIC, PawprintModeBudget,
    REPEATABLE_MODE_CAPABILITY, REPEATABLE_MODE_FIELD, REPEATABLE_MODE_MECHANIC, FixedRepeatableModeChoices,
)

_REPEATABLE_MODE_OWNERS = (
    (PAWPRINT_FIELD, PAWPRINT_CAPABILITY, PAWPRINT_MECHANIC, PawprintModeBudget),
    (REPEATABLE_MODE_FIELD, REPEATABLE_MODE_CAPABILITY, REPEATABLE_MODE_MECHANIC, FixedRepeatableModeChoices),
)


def _mode_owner(schema, mechanics, capabilities=()):
    found = tuple(owner for owner in _REPEATABLE_MODE_OWNERS
                  if owner[0] in schema or owner[1] in capabilities or owner[2] in mechanics)
    if len(found) > 1:
        raise ValueError("A resolving spell requires one repeatable mode selection contract")
    return found[0] if found else None


def pawprint_capabilities(*, effects, target_schema, mechanics, cost_schema, modal_resolver):
    if not isinstance(cost_schema, Mapping) or effects:
        return ()
    try:
        owner = _mode_owner(cost_schema, mechanics)
        if owner is None:
            return ()
        field, capability, mechanic, model = owner
        if set(cost_schema) != {field} or not {mechanic, "fixed-nonrepeating-modal"} <= set(mechanics):
            return ()
        model.from_dict(cost_schema[field]).validate_schema(target_schema)
    except (TypeError, ValueError):
        return ()
    owned = modal_resolver(
        effects=effects, target_schema=target_schema,
        mechanics=tuple(value for value in mechanics if value != mechanic),
        allow_pawprint_draw=field == PAWPRINT_FIELD,
    )
    return (*owned, capability) if owned else ()


def pawprint_runtime_dependencies(program):
    schema = program.cost_schema or {}
    owner = _mode_owner(schema, program.coverage, program.capability_dependencies)
    if owner is None:
        return ()
    field, capability, mechanic, model = owner
    if (set(schema) != {field} or mechanic not in program.coverage or program.effects or program.handlers
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.event_condition is not None):
        raise ValueError("A repeatable mode selection requires its complete resolving spell")
    model.from_dict(schema[field]).validate_schema(program.target_schema)
    return (capability,)
