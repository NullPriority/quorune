from __future__ import annotations

"""CR 702.29d binds the combined wording to the single discard occurrence."""

import re
from .fixed_public_event_trigger_bindings import _all_conditions, _spec


CYCLE_OR_DISCARD_CAPABILITY = 'trigger.event.cycle_or_discard'
_COMBINED = re.compile(r'^Whenever you cycle or discard (?P<object>a|another) card, (?P<body>.+)$', re.I)


def cycle_or_discard_binding_spec(material_line):
    match = _COMBINED.fullmatch(material_line)
    if match is None:
        return None
    conditions = [{'field': 'previous_controller', 'op': 'eq', 'value': '$source.controller'}]
    if match['object'].casefold() == 'another':
        conditions.append({'field': 'card', 'op': 'ne', 'value': '$source.ref'})
    # Cycling already discards as a cost. Subscribing separately to cycling
    # would incorrectly produce a second trigger for that same card.
    return _spec('card.discarded', 'controller_discards_card', match['body'],
        'fixed-counter-public-zone-trigger-v1', 'trigger-event-normalized-zone-change',
        condition=_all_conditions(*conditions), capabilities=(CYCLE_OR_DISCARD_CAPABILITY,))
