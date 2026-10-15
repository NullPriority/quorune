from __future__ import annotations

"""Reuse the closed public query grammar only at an applied instruction."""

import re
from ..live_battlefield_quantity_model import LiveBattlefieldQuantitySpec


def resolution_quantity(value: str, *, source_name: str, complete_value: bool = False):
    from .query_characteristic_templates import query_characteristic_quantity, query_characteristic_value
    quantity = (query_characteristic_value(value, source_name=source_name) if complete_value
        else query_characteristic_quantity(value, source_name=source_name, definition_extensions=True))
    if quantity is not None:
        return quantity
    text = value.strip().rstrip('.')
    if complete_value:
        match = re.fullmatch(r'(?:the )?number of (?P<objects>.+)', text, re.I)
        if match is None:
            return None
        text = match['objects']
    from .public_state_queries import fixed_battlefield_query_subject
    parsed = fixed_battlefield_query_subject(text)
    if parsed is None:
        return None
    try:
        return LiveBattlefieldQuantitySpec(parsed[0], parsed[1], parsed[2])
    except ValueError:
        return None
