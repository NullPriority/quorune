from __future__ import annotations

"""Closed static devotion adjustments and existing current-quantity grammar."""

import re

from ..devotion import DEVOTION_CAPABILITY, DEVOTION_EVENT, DEVOTION_MODIFIER_HANDLER
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


def fixed_devotion_increase_handler(text: str):
    match=re.fullmatch(rf'Your devotion to each color and each combination of colors is increased by (?P<amount>{FIXED_COUNT_PATTERN})\.?',text.strip(),re.I)
    if match is None:return None
    amount=fixed_number(match['amount'])
    if not 1<=amount<=10:return None
    return 'fixed-devotion-increase-v1', {
        'handler_id':DEVOTION_MODIFIER_HANDLER,'schema_version':1,'event':DEVOTION_EVENT,'amount':amount,
    }, (DEVOTION_CAPABILITY,)
