"""Offer one sealed defending player a draw through the existing draw leaf."""
from . import regex_working_set as re

EVENT_OPTIONAL_DRAW_FIELD = 'event_optional_draw_version'
EVENT_OPTIONAL_DRAW_CAPABILITY = 'resolution.event_player.optional_draw'
EVENT_OPTIONAL_DRAW_MECHANIC = 'fixed-event-player-optional-draw'
EVENT_OPTIONAL_DRAW_TEMPLATE = 'fixed-defending-player-optional-draw-v1'


def fixed_event_optional_draw_result(binding, body, *, effect_template, card_name):
    from .event_creature_controller_results import dead_creature_controller_optional_draw_result
    if death := dead_creature_controller_optional_draw_result(binding, body):return death
    if binding.event.value != 'creature.attacks' or binding.variant != 'source_attacks':return None
    match = re.fullmatch(r'defending player may draw (?P<body>.+)', body.strip(), re.I)
    if match is None:return None
    from .event_player_results import _event_player, EVENT_PLAYER_RESULT_MECHANIC
    player = _event_player(binding, 'defending player')
    if player != '$context.defending_player':return None
    from .draw_templates import fixed_draw_effect_template
    compiled = fixed_draw_effect_template('You may draw ' + match['body'])
    if compiled is None:return None
    name, effects, schema, mechanics = compiled
    if name != 'optional-draw-controller-v1' or len(effects) != 1 or schema is not None:return None
    effect, = effects
    if (set(effect) != {'op', 'player', 'drawer', 'count', 'private'}
            or effect.get('op') != 'offer_draw' or effect.get('player') != '$controller'
            or effect.get('drawer') != '$controller' or type(effect.get('count')) is not int
            or effect['count'] < 1 or effect.get('private') is not True):return None
    result = {**effect, 'player': player, 'drawer': player, EVENT_OPTIONAL_DRAW_FIELD: 1}
    return ((EVENT_OPTIONAL_DRAW_TEMPLATE, (result,), None,
        (*mechanics, EVENT_PLAYER_RESULT_MECHANIC, EVENT_OPTIONAL_DRAW_MECHANIC)), False)
