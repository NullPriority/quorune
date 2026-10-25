from __future__ import annotations

"""Closed source productions for static counter-placement prohibitions."""

import re
from ..counter_permission import (
    COUNTER_PROHIBITION_CAPABILITY, COUNTER_PROHIBITION_EVENT,
    COUNTER_PROHIBITION_HANDLER, FixedCounterProhibitionSpec,
)
from ..rules.source_references import SourceReferenceSpec


def fixed_counter_prohibition(text, *, source_name):
    if type(text) is not str or type(source_name) is not str:
        return None
    line = ' '.join(text.strip().rstrip('.').split()).replace('’', "'")
    low = line.casefold()
    self_match = re.fullmatch(r"(.+?) can't have counters put on (?:it|him|her)", line, re.I)
    if self_match:
        subject = self_match.group(1)
        if subject.casefold() not in {'this creature', 'this permanent'}:
            try:
                if not SourceReferenceSpec(source_name).matches(subject):
                    return None
            except ValueError:
                return None
        return FixedCounterProhibitionSpec('permanent', 'self')
    if low == "you can't get poison counters":
        return FixedCounterProhibitionSpec('player', 'you', 'poison')
    if low == "players can't get counters":
        return FixedCounterProhibitionSpec('player', 'any')
    if low == "creatures you control can't have -1/-1 counters put on them":
        return FixedCounterProhibitionSpec('permanent', 'you', '-1/-1', ('creature',))
    if low == "creatures your opponents control can't have +1/+1 counters put on them":
        return FixedCounterProhibitionSpec('permanent', 'opponent', '+1/+1', ('creature',))
    if low == "counters can't be put on artifacts, creatures, enchantments, or lands":
        return FixedCounterProhibitionSpec('permanent', 'any', None,
            ('artifact', 'creature', 'enchantment', 'land'))
    return None


def static_counter_prohibition_handler(text, *, source_name):
    spec = fixed_counter_prohibition(text, source_name=source_name)
    if spec is None:
        return None
    from ..ability_fragments import ability_fragment_to_dict
    return ('static-fixed-counter-prohibition-v1',
        dict(handler_id=COUNTER_PROHIBITION_HANDLER, schema_version=1,
            event=COUNTER_PROHIBITION_EVENT, fragment=ability_fragment_to_dict(spec)),
        COUNTER_PROHIBITION_CAPABILITY)
