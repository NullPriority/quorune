"""Closed sacrifice choices addressed by the established occurrence-player binder."""
from __future__ import annotations

from copy import deepcopy
import re

from .affected_player_sacrifice_templates import fixed_affected_player_sacrifice_effect_template
from .event_player_results import _event_player, _occurrence_player


EVENT_PLAYER_CHOICE_CAPABILITY = 'resolution.event_player.fixed_sacrifice'
EVENT_PLAYER_CHOICE_MECHANIC = 'fixed-event-player-sacrifice'


def _sacrifice_clause(body):
    return re.fullmatch(r"(?P<subject>that player|they|defending player|that creature['’]s controller) "
                        r"sacrifices? (?P<body>.+)", body.strip(), re.I)


def _compiled_sacrifice(match, player):
    if player is None or player == '$controller':
        return None
    template = fixed_affected_player_sacrifice_effect_template('Target player sacrifices ' + match['body'])
    if template is None:
        return None
    effect = deepcopy(dict(template.effects[0]))
    if effect.get('players') != ['$target.0'] or effect.pop('target', None) != '$target.0':
        return None
    effect['players'] = [player]
    return ('fixed-event-player-sacrifice-v1', (effect,), None,
            tuple(dict.fromkeys((*(mechanic for mechanic in template.mechanics if mechanic != 'cr-115-targets'),
                                 EVENT_PLAYER_CHOICE_MECHANIC))))


def fixed_event_player_sacrifice_result(binding, body, *, card_name, effect_template):
    del card_name, effect_template
    match = _sacrifice_clause(body)
    if match is None:
        return None
    compiled = _compiled_sacrifice(match, _event_player(binding, match['subject'].casefold()))
    return (compiled, False) if compiled is not None else None


def source_death_player_sacrifice_result(body, *, event_phrase):
    """Adapt the existing source-self death owner to the same LKI player table."""
    match = _sacrifice_clause(body)
    if event_phrase != 'dies' or match is None:
        return None
    return _compiled_sacrifice(match, _occurrence_player(
        'creature.dies', None, 'source_dies', match['subject'].casefold()))
