from __future__ import annotations

"""Fixed controller Blight and its printed draw sequence."""

import re

from ..rules.blight_model import BLIGHT_MECHANIC, BLIGHT_OPERATION
from .fixed_numbers import FIXED_COUNT_PATTERN


def fixed_blight_effect_template(text):
    match = re.fullmatch(r"Blight (?P<amount>[1-9][0-9]*)\.?", text.strip(), re.I)
    if match is None:
        return None
    return (
        "fixed-controller-blight-v1",
        ({"op": BLIGHT_OPERATION, "schema_version": 1, "player": "$controller", "amount": int(match["amount"])},),
        None,
        (BLIGHT_MECHANIC,),
    )


def normalized_blight_sequence(text):
    match = re.fullmatch(
        rf"(?P<draw>(?:You )?draw (?:{FIXED_COUNT_PATTERN}) cards?) and (?P<blight>blight [1-9][0-9]*)\.",
        text.strip(), re.I,
    )
    return f"{match['draw']}. {match['blight']}." if match is not None else text
