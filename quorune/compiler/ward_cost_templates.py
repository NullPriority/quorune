from __future__ import annotations

"""Closed Oracle grammar for fixed public Ward payments."""

import re

from ..trigger_participation import WardSpec
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


_FIXED_GENERIC_WARD = re.compile(
    r"Ward\s+\{(?P<generic>\d+)\}\.?",
    re.IGNORECASE,
)
_FIXED_LIFE_WARD = re.compile(
    rf"Ward[—-]Pay (?P<amount>{FIXED_COUNT_PATTERN}|\d+) life\.?",
    re.IGNORECASE,
)
_DISCARD_ONE_WARD = re.compile(
    r"Ward[—-]Discard a card\.?",
    re.IGNORECASE,
)


def fixed_ward_spec(text: str) -> WardSpec | None:
    """Parse one exact generic, fixed-life, or discard-one Ward cost."""

    material = text.strip()
    generic = _FIXED_GENERIC_WARD.fullmatch(material)
    if generic is not None:
        return WardSpec(generic_cost=int(generic.group("generic")))
    life = _FIXED_LIFE_WARD.fullmatch(material)
    if life is not None:
        amount = fixed_number(life.group("amount"))
        return WardSpec(life_payment=amount) if amount > 0 else None
    if _DISCARD_ONE_WARD.fullmatch(material) is not None:
        return WardSpec(discard_cards=1)
    return None


__all__ = ["fixed_ward_spec"]
