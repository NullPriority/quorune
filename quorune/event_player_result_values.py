from __future__ import annotations

"""Closed values permitted by already-bound event-player result layouts."""

from .fractional_quantity_model import HalfQuantitySpec, HALF_LIFE_CAPABILITY


def event_result_value_capabilities(operation, field, value):
    if type(value) is int:
        return () if value > 0 else None
    if operation != 'lose_life' or field != 'amount':
        return None
    try:
        HalfQuantitySpec.from_dict(value)
    except (TypeError, ValueError):
        return None
    return (HALF_LIFE_CAPABILITY,)
