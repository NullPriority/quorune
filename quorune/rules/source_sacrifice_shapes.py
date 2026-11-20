"""Re-derive source-sacrifice authority from the versioned executable payload."""
from collections.abc import Mapping

from ..source_sacrifice_model import (
    FORMER_ATTACHMENT_SACRIFICE_VERSION, SOURCE_SACRIFICE_FIELD, SOURCE_SACRIFICE_MECHANIC,
    UNATTACHMENT_EVENT, fixed_source_sacrifice_dependencies,
)


def fixed_source_sacrifice_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None or SOURCE_SACRIFICE_MECHANIC not in mechanic_ids:
        return ()
    try:
        return fixed_source_sacrifice_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def fixed_source_sacrifice_runtime_dependencies(program):
    required = set()
    from_unattachment = False

    def visit(value):
        nonlocal from_unattachment
        if isinstance(value, Mapping):
            required.update(fixed_source_sacrifice_dependencies(value))
            from_unattachment |= value.get(SOURCE_SACRIFICE_FIELD) == FORMER_ATTACHMENT_SACRIFICE_VERSION
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit((program.effects, program.handlers))
    if required and program.active_zone != 'battlefield':
        raise ValueError('Fixed source sacrifice requires its represented battlefield source')
    if from_unattachment and (
        program.event != UNATTACHMENT_EVENT or program.target_schema is not None
        or getattr(program, 'cost_schema', None) is not None or program.handlers or len(program.effects) != 1
        or program.effects[0].get(SOURCE_SACRIFICE_FIELD) != FORMER_ATTACHMENT_SACRIFICE_VERSION
        or program.event_condition != {'field': 'card', 'op': 'eq', 'value': '$source.ref'}
    ):
        raise ValueError('Former attachment sacrifice requires its single untargeted source unattachment frame')
    return tuple(sorted(required))


def is_closed_fixed_source_sacrifice_program(program):
    required = fixed_source_sacrifice_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)
