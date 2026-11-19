"""Consume one complete optional draw addressed to a dead creature's controller."""
from . import regex_working_set as re
from .draw_templates import fixed_draw_effect_template
from .event_optional_draw_results import EVENT_OPTIONAL_DRAW_FIELD as FIELD, EVENT_OPTIONAL_DRAW_MECHANIC
from .event_player_results import EVENT_PLAYER_RESULT_MECHANIC
from ..rules.event_creature_controller_results import (
    DEATH_CONTROLLER, DEATH_CONTROLLER_DRAW_VERSION, death_controller_draw_condition_is_closed,
    death_controller_draw_payload_dependencies,
)


def dead_creature_controller_optional_draw_result(binding, body):
    if (binding.event.value != 'creature.dies' or binding.active_zone != 'battlefield'
            or binding.variant != 'creature:any:including_source:any_object'
            or not death_controller_draw_condition_is_closed(binding.event_condition)):
        return None
    match = re.fullmatch(r"that creature['’]s controller may draw (?P<draw>.+)", body.strip(), re.I)
    if match is None:return None
    compiled = fixed_draw_effect_template('You may draw ' + match['draw'])
    if compiled is None or compiled[0] != 'optional-draw-controller-v1' or len(compiled[1]) != 1 or compiled[2] is not None:
        return None
    effect = {**compiled[1][0], 'player': DEATH_CONTROLLER, 'drawer': DEATH_CONTROLLER,
        FIELD: DEATH_CONTROLLER_DRAW_VERSION}
    try:death_controller_draw_payload_dependencies(effect)
    except (KeyError, TypeError, ValueError):return None
    return (('dead-creature-controller-optional-draw-v2', (effect,), None,
        tuple(dict.fromkeys((*compiled[3], EVENT_PLAYER_RESULT_MECHANIC, EVENT_OPTIONAL_DRAW_MECHANIC)))), False)
