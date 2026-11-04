"""Completed creature exploration over the existing typed action owner."""
import re
from ..explore_action_model import CREATURE_EXPLORE_EVENT, CREATURE_EXPLORE_CAPABILITY
from .fixed_public_event_trigger_bindings import _spec

CREATURE_EXPLORE_VARIANT = 'controlled_creature_explores'


def explore_event_binding_spec(text, *, card_name=None):
    del card_name
    match = re.fullmatch(r'Whenever a creature you control explores, (?P<body>.+)', text, re.IGNORECASE)
    if match is None:
        return None
    return _spec(CREATURE_EXPLORE_EVENT, CREATURE_EXPLORE_VARIANT, match['body'],
        'fixed-counter-creature-explore-trigger-v1', 'trigger-event-normalized-public-action',
        condition={'field': 'controller', 'op': 'eq', 'value': '$source.controller'},
        capabilities=(CREATURE_EXPLORE_CAPABILITY,))
