"""Whole fixed-count controller ordering and explicit controller shuffle."""
import re

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..library_order_model import (
    FixedTopLibraryOrderSpec,
    FixedHandTopOrderSpec,
    HAND_TOP_ORDER_MECHANIC,
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
    hand_top = re.fullmatch(
        rf"Put (?P<count>{FIXED_COUNT_PATTERN}) (?P<noun>cards?) from your hand "
        r"on top of your library(?: in any order)?\.",
        text.strip(), re.I,
    )
    if hand_top is not None:
        try:
            spec = FixedHandTopOrderSpec(fixed_number(hand_top["count"]))
        except ValueError:
            return None
        if (hand_top["noun"].casefold() == "card") != (spec.count == 1):
            return None
        return "fixed-hand-to-library-top-v1", (spec.effect(),), None, (HAND_TOP_ORDER_MECHANIC,)
    if re.fullmatch(r'Shuffle(?: your library)?\.', text.strip(), re.I):
        return (
            'fixed-controller-shuffle-v1', (fixed_shuffle_effect(),), None,
            (SHUFFLE_MECHANIC,),
        )
    return None
