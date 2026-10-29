"""Whole fixed-count controller ordering and explicit controller shuffle."""
import re

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..library_order_model import (
    FixedTopLibraryOrderSpec,
    TOP_ORDER_MECHANIC,
    SHUFFLE_MECHANIC,
    fixed_shuffle_effect,
)


def fixed_top_order_template(text: str):
    match = re.fullmatch(
        rf'Look at the top (?P<count>{FIXED_COUNT_PATTERN}) cards? of your '
        r'library,? then put them back in any order\.',
        text.strip(),
        re.I,
    )
    if match is not None:
        try:
            spec = FixedTopLibraryOrderSpec(fixed_number(match['count']))
        except ValueError:
            return None
        return (
            'fixed-controller-top-order-v1', (spec.effect(),), None,
            (TOP_ORDER_MECHANIC,),
        )
    if re.fullmatch(r'Shuffle(?: your library)?\.', text.strip(), re.I):
        return (
            'fixed-controller-shuffle-v1', (fixed_shuffle_effect(),), None,
            (SHUFFLE_MECHANIC,),
        )
    return None
