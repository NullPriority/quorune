from __future__ import annotations

"""Closed single-named-counter doubling over existing placement subjects."""

import re
from ..counter_names import existing_counter_amount_descriptor
from .counter_placement_templates import FIXED_COUNTER_NAME_PATTERN, fixed_counter_placement_effect_template, fixed_counter_placement_set_effect_template

COUNTER_DOUBLING_CAPABILITY = "counter.producer.named_doubling"
COUNTER_DOUBLING_MECHANIC = "named-counter-doubling"
_DOUBLING = re.compile(rf"Double the number of (?P<counter>{FIXED_COUNTER_NAME_PATTERN}) counters on (?P<subject>.+?)\.?", re.I)


def named_counter_doubling_effect_template(text, *, card_name, source_attachment_relation=None):
    match = _DOUBLING.fullmatch(text.strip())
    if match is None:
        return None
    name = ' '.join(match.group('counter').casefold().split())
    if name in {'each kind of','each type of','all','any','those','chosen','the chosen'}:
        return None
    placement = f"Put a {name} counter on {match.group('subject')}."
    base = fixed_counter_placement_effect_template(placement, card_name=card_name, source_attachment_relation=source_attachment_relation)
    if base is None:
        base = fixed_counter_placement_set_effect_template(placement)
    if base is None:
        return None
    template, effects, target_schema, mechanics = base.compiled()
    return ('named-counter-doubling-'+template, tuple({**effect,'amount':existing_counter_amount_descriptor()} for effect in effects),
            target_schema, (COUNTER_DOUBLING_MECHANIC,*mechanics))
