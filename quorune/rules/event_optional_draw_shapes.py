"""Bind optional draw choice and recipient to one supported attack occurrence."""
from collections.abc import Mapping
from ..compiler.event_optional_draw_results import (
    EVENT_OPTIONAL_DRAW_FIELD, EVENT_OPTIONAL_DRAW_CAPABILITY, EVENT_OPTIONAL_DRAW_MECHANIC,
)


def validate_event_optional_draw_payload(effect, *, materialized=False):
    if isinstance(effect, Mapping) and effect.get(EVENT_OPTIONAL_DRAW_FIELD) == 2:
        from .event_creature_controller_results import death_controller_draw_payload_dependencies
        return death_controller_draw_payload_dependencies(effect, materialized=materialized)
    if (not isinstance(effect, Mapping)
            or set(effect) != {'op', 'player', 'drawer', 'count', 'private', EVENT_OPTIONAL_DRAW_FIELD}
            or effect.get('op') != 'offer_draw' or type(effect.get(EVENT_OPTIONAL_DRAW_FIELD)) is not int
            or effect[EVENT_OPTIONAL_DRAW_FIELD] != 1 or type(effect.get('count')) is not int
            or effect['count'] < 1 or effect.get('private') is not True):
        raise ValueError('Event optional draw requires the complete version-one private fixed draw')
    player = effect.get('player')
    if player != effect.get('drawer'):
        raise ValueError('The sealed event participant must both choose and draw')
    if materialized:
        if type(player) is not str or not player or player.startswith('$'):
            raise ValueError('The event draw participant must already be materialized')
    elif player != '$context.defending_player':
        raise ValueError('Only the represented defending-player reference is admitted')
    return (EVENT_OPTIONAL_DRAW_CAPABILITY, 'resolution.event_player.fixed_result', 'zone.draw.library_to_hand')


def event_optional_draw_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) == 1 and isinstance(effects[0], Mapping) and effects[0].get(EVENT_OPTIONAL_DRAW_FIELD) == 2:
        from .event_creature_controller_results import death_controller_draw_node_capabilities
        return death_controller_draw_node_capabilities(effects=effects, target_schema=target_schema)
    if (len(effects) != 1 or target_schema is not None
            or EVENT_OPTIONAL_DRAW_MECHANIC not in mechanic_ids):return ()
    try:return validate_event_optional_draw_payload(effects[0])
    except (TypeError, ValueError):return ()


def _event_is_closed(program):
    from .event_player_result_shapes import _source_combat_player_scope
    return (program.event == 'creature.attacks' and program.active_zone == 'battlefield'
            and program.target_schema is None and 'one_or_more_event_batch' not in program.coverage
            and _source_combat_player_scope(program.event_condition))


def event_optional_draw_runtime_dependencies(program):
    from .event_creature_controller_results import death_controller_draw_program_dependencies
    if death := death_controller_draw_program_dependencies(program):return death
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if EVENT_OPTIONAL_DRAW_FIELD in value:
                required.update(validate_event_optional_draw_payload(value))
                if not _event_is_closed(program):
                    raise ValueError('The defending-player draw requires its original source attack occurrence')
                required.update(('trigger.effect.fixed_event', 'trigger.event.normalized_self_attack', 'trigger.placement.apnap'))
            for child in value.values():visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))


def is_closed_event_optional_draw_program(program):
    from .event_creature_controller_results import death_controller_draw_program_dependencies
    try:death = death_controller_draw_program_dependencies(program)
    except (KeyError, TypeError, ValueError):return False
    if death:return set(death) <= set(program.capability_dependencies)
    deps = event_optional_draw_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(deps) and _event_is_closed(program) and set(deps) <= set(program.capability_dependencies)
