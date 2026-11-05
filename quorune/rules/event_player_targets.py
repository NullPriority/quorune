"""Bind a target controller from the same closed occurrence at both boundaries."""

from collections.abc import Mapping

from ..target_forms import EVENT_PLAYER_TARGET_REFERENCES
from .event_player_result_shapes import _event_player_reference_is_closed


def bound_event_player_controller_schema(schema, context):
    if not isinstance(schema, Mapping):
        return schema
    reference = schema.get('controller_seat')
    if reference not in EVENT_PLAYER_TARGET_REFERENCES:
        return schema
    player = context.get(reference.removeprefix('$context.'))
    if type(player) is not str or not player or player.startswith('$'):
        raise ValueError('Event-player target binding is unavailable')
    return {**schema, 'controller_seat': player}


def event_player_target_runtime_dependencies(program):
    schema = program.target_schema
    if not isinstance(schema, Mapping) or schema.get('controller_seat') not in EVENT_PLAYER_TARGET_REFERENCES:
        return ()
    from .provoke_shapes import provoke_program_runtime_dependencies
    provoke = provoke_program_runtime_dependencies(program)
    if provoke:
        return provoke
    from ..compiler.direct_target import DirectPermanentTargetSpec
    DirectPermanentTargetSpec.from_target_schema(schema)
    if program.active_zone != 'battlefield' or not _event_player_reference_is_closed(program, schema['controller_seat']):
        raise ValueError('Event-player target lacks its closed occurrence scope')
    return ('target.revalidate_resolution',)
