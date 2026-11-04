"""Bind one existing direct-permanent target to a proved occurrence player."""

from dataclasses import replace
import re

from .direct_target import DirectPermanentTargetSpec
from .event_player_results import _event_player
from ..target_forms import EVENT_PLAYER_TARGET_REFERENCES


def event_player_target_result(binding, body, *, compile_result):
    matches = tuple(re.finditer(r'\b(?P<player>defending player|that player) controls\b', body, re.I))
    if len(matches) != 1:
        return None
    match = matches[0]
    subject = match['player'].casefold()
    player = _event_player(binding, subject)
    if player not in EVENT_PLAYER_TARGET_REFERENCES:
        return None
    normalized = body[:match.start()] + 'you control' + body[match.end():]
    result = compile_result(normalized)
    if result is None:
        return None
    compiled, source_result = result
    template, effects, schema, mechanics = compiled
    try:
        target = DirectPermanentTargetSpec.from_target_schema(schema)
    except (TypeError, ValueError):
        return None
    if target.controller_relation != 'you' or target.controller_seat is not None:
        return None
    target = replace(target, controller_relation='any', controller_seat=player)
    suffix = '-defending-player' if subject == 'defending player' else '-event-player'
    return (template + suffix, effects, target.to_target_schema(), mechanics), source_result
