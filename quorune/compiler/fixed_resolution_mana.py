from __future__ import annotations

"""Closed fixed symbol bundles through the existing resolution mana owner."""

from collections import Counter
import re
from ..abilities import _mana_spend_restriction

FIXED_RESOLUTION_MANA_MECHANIC = 'fixed-resolution-mana'
FIXED_RESOLUTION_MANA_CAPABILITY = 'mana.production.fixed_resolution'


def fixed_resolution_mana_effect_template(text: str):
    restriction = None
    if '. Spend this mana only to ' in text:
        body, _tail = text.split('. Spend this mana only to ', 1)
        restriction = _mana_spend_restriction(text)
        if restriction is None:
            return None
        text = body + '.'
    match = re.fullmatch(r'Add (?P<symbols>(?:\{[WUBRGC]\})+)\.?', text.strip(), re.I)
    if match is None:
        return None
    symbols = re.findall(r'\{([WUBRGC])\}', match['symbols'].upper())
    if not 1 <= len(symbols) <= 10:
        return None
    counts = Counter(symbols)
    effects = tuple(
        {
            'op': 'mana', 'player': '$controller', 'color': color,
            'amount': counts[color], 'source': '$source',
            **({'spend_restriction': restriction} if restriction else {}),
        }
        for color in 'WUBRGC' if counts[color]
    )
    return 'fixed-resolution-mana-bundle-v1', effects, None, (FIXED_RESOLUTION_MANA_MECHANIC,)
