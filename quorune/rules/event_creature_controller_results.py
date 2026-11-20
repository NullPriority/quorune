"""Closed optional draw for the last battlefield controller of a dead creature."""
from collections.abc import Mapping
from ..replacement.immutable import FrozenMap, thaw_value
from ..compiler.event_optional_draw_results import EVENT_OPTIONAL_DRAW_FIELD as FIELD

DEATH_CONTROLLER = '$context.previous_controller'
DEATH_CONTROLLER_DRAW_VERSION = 2
_DEATH_CONDITION = FrozenMap({'field': 'token', 'op': 'in', 'value': [False, True]})
_CAPABILITIES = ('resolution.event_player.optional_draw', 'resolution.event_player.fixed_result',
    'zone.draw.library_to_hand')


def death_controller_draw_payload_dependencies(effect, *, materialized=False):
    if (not isinstance(effect, Mapping)
            or set(effect) != {'op', 'player', 'drawer', 'count', 'private', FIELD}
            or effect.get('op') != 'offer_draw' or type(effect.get(FIELD)) is not int
            or effect[FIELD] != DEATH_CONTROLLER_DRAW_VERSION or type(effect.get('count')) is not int
            or effect['count'] <= 0 or effect.get('private') is not True
            or effect.get('player') != effect.get('drawer')):
        raise ValueError('Death-controller optional draw requires its complete version-two fixed private draw')
    player = effect['player']
    if (materialized and (type(player) is not str or not player or player.startswith('$'))
            or not materialized and player != DEATH_CONTROLLER):
        raise ValueError('Death-controller draw chooser and drawer must be the original creature controller')
    return _CAPABILITIES


def death_controller_draw_is_unavailable(effect, *, actor, seats, active_seats):
    death_controller_draw_payload_dependencies(effect, materialized=True)
    if effect['player'] != actor or actor not in seats:
        raise ValueError('Death-controller draw requires its known original prospective drawer')
    return actor not in active_seats


def death_controller_draw_node_capabilities(*, effects, target_schema, cost_schema=None, **kwargs):
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:return death_controller_draw_payload_dependencies(effects[0])
    except (KeyError, TypeError, ValueError):return ()


def _contains_death_draw(value):
    if isinstance(value, Mapping):
        return (value.get(FIELD) == DEATH_CONTROLLER_DRAW_VERSION
            or any(_contains_death_draw(v) for v in value.values()))
    return isinstance(value, (tuple, list)) and any(_contains_death_draw(v) for v in value)


def death_controller_draw_condition_is_closed(condition):
    raw = thaw_value(condition)
    return (raw == thaw_value(_DEATH_CONDITION) and isinstance(raw, Mapping)
        and all(type(v) is bool for v in raw.get('value', ())))


def death_controller_draw_program_dependencies(program):
    if not _contains_death_draw((program.effects, getattr(program, 'handlers', ()),
            program.target_schema, getattr(program, 'cost_schema', None))):
        return ()
    if (program.event != 'creature.dies' or program.active_zone != 'battlefield'
            or getattr(program, 'handlers', ()) or not death_controller_draw_condition_is_closed(program.event_condition)):
        raise ValueError('Death-controller optional draw requires the complete all-creature death occurrence')
    required = death_controller_draw_node_capabilities(effects=program.effects, target_schema=program.target_schema,
        cost_schema=getattr(program, 'cost_schema', None))
    if not required:
        raise ValueError('Death-controller draw must remain one complete untargeted and unpriced instruction')
    return tuple(sorted({*required, 'trigger.effect.fixed_event',
        'trigger.event.normalized_zone_change', 'trigger.placement.apnap'}))


def is_closed_death_controller_draw_program(program):
    try:required = death_controller_draw_program_dependencies(program)
    except (KeyError, TypeError, ValueError):return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
