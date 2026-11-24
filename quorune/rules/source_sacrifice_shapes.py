"""Re-derive source-sacrifice authority from the versioned executable payload."""
from collections.abc import Mapping

from ..source_sacrifice_model import (
    CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION, FORMER_ATTACHMENT_SACRIFICE_VERSION,
    SOURCE_SACRIFICE_FIELD, SOURCE_SACRIFICE_MECHANIC,
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
    versions = set()

    def visit(value):
        if isinstance(value, Mapping):
            required.update(fixed_source_sacrifice_dependencies(value))
            if SOURCE_SACRIFICE_FIELD in value:
                versions.add(value[SOURCE_SACRIFICE_FIELD])
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit((program.effects, program.handlers))
    if required and program.active_zone != 'battlefield':
        raise ValueError('Fixed source sacrifice requires its represented battlefield source')
    if FORMER_ATTACHMENT_SACRIFICE_VERSION in versions and (
        program.event != UNATTACHMENT_EVENT or program.target_schema is not None
        or getattr(program, 'cost_schema', None) is not None or program.handlers or len(program.effects) != 1
        or program.effects[0].get(SOURCE_SACRIFICE_FIELD) != FORMER_ATTACHMENT_SACRIFICE_VERSION
        or program.event_condition != {'field': 'card', 'op': 'eq', 'value': '$source.ref'}
    ):
        raise ValueError('Former attachment sacrifice requires its single untargeted source unattachment frame')
    if CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION in versions and (
        program.event != 'spell.cast' or program.event_condition is not None
        or program.target_schema is not None or getattr(program, 'cost_schema', None) is not None
        or program.handlers or len(program.effects) != 1
        or program.effects[0].get(SOURCE_SACRIFICE_FIELD) != CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION
    ):
        raise ValueError('Source sacrifice draw requires its single unqualified spell-cast frame')
    return tuple(sorted(required))


def is_closed_fixed_source_sacrifice_program(program):
    required = fixed_source_sacrifice_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    if required and program.effects[0].get(SOURCE_SACRIFICE_FIELD) == CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION:
        try:
            fixed_source_sacrifice_runtime_dependencies(program)
        except (TypeError, ValueError):
            return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
