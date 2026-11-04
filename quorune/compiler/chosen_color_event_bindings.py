"""Spell-cast subscriptions over an existing per-object color designation."""
import re

from ..rules.chosen_color_events import CHOSEN_COLOR_CAST_CAPABILITY
from .fixed_public_event_trigger_bindings import _spec


CHOSEN_COLOR_CAST_VARIANTS = frozenset({'controller_casts_chosen_color', 'player_casts_chosen_color'})


def chosen_color_event_binding_spec(text, *, card_name=None):
    del card_name
    match = re.fullmatch(
        r'Whenever (?P<actor>you cast|a player casts) a spell of the chosen color, (?P<body>.+)',
        text, re.IGNORECASE)
    if match is None:
        return None
    conditions = [{'field': 'source_annotation.chosen_color', 'op': 'in', 'value': '$context.colors'}]
    controller = match['actor'].casefold() == 'you cast'
    if controller:
        conditions.append({'field': 'controller', 'op': 'eq', 'value': '$source.controller'})
    return _spec('spell.cast', ('controller' if controller else 'player') + '_casts_chosen_color',
        match['body'], 'fixed-counter-spell-cast-characteristic-trigger-v1',
        'trigger-event-normalized-spell-cast',
        condition=conditions[0] if len(conditions) == 1 else {'all': conditions},
        capabilities=(CHOSEN_COLOR_CAST_CAPABILITY,))
