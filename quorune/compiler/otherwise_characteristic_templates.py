from __future__ import annotations

"""Complete fixed additive Aura branches over existing characteristic owners."""

import re

from .continuous_templates import (
    attached_fixed_characteristics_handler, fixed_public_state_characteristics_handler,
)


def attached_otherwise_stat_handler(text: str, *, source_name: str):
    match = re.fullmatch(
        r'Enchanted creature gets (?P<then_p>[+-][0-9]+)/(?P<then_t>[+-][0-9]+) '
        r'as long as (?P<condition>[^.]+)\. Otherwise, it gets '
        r'(?P<else_p>[+-][0-9]+)/(?P<else_t>[+-][0-9]+)\.?', text.strip(), re.I)
    if match is None:
        return None
    condition, count = re.subn(r"^it(?:'s| is)\s+", 'enchanted creature is ',
        match['condition'], count=1, flags=re.I)
    if count != 1:
        return None
    otherwise = (int(match['else_p']), int(match['else_t']))
    correction = (int(match['then_p']) - otherwise[0], int(match['then_t']) - otherwise[1])
    base = attached_fixed_characteristics_handler(
        f'Enchanted creature gets {otherwise[0]:+d}/{otherwise[1]:+d}.', source_name=source_name)
    # Even equal branches require a separately recognized condition; a sentinel
    # modifier validates its complete grammar without executing a dummy effect.
    p, t = correction if any(correction) else (1, 0)
    conditional = fixed_public_state_characteristics_handler(
        f'As long as {condition}, enchanted creature gets {p:+d}/{t:+d}.', source_name=source_name)
    if base is None or conditional is None:
        return None
    handlers = (base[1], conditional[1]) if any(correction) else (base[1],)
    return ('attached-otherwise-fixed-stats-v1', handlers, tuple(sorted(set(base[2]) | set(conditional[2]))))


def composed_public_state_characteristics_handler(text: str, *, source_name: str):
    return (attached_otherwise_stat_handler(text, source_name=source_name)
        or fixed_public_state_characteristics_handler(text, source_name=source_name))
