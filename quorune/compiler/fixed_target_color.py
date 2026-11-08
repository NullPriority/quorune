"""Lower a fixed color setting for one spell or permanent."""

import re

from ..resolution_characteristic_model import (
    CHARACTERISTIC_OPERATION, FIXED_TARGET_COLOR_MECHANIC, FIXED_TARGET_COLOR_VERSION,
    fixed_target_color_schema,
)


_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
_INSTRUCTION = re.compile(
    r"Target (?:spell or permanent|permanent or spell) becomes "
    r"(?P<color>white|blue|black|red|green|colorless)(?P<temporary> until end of turn)?\.?",
    re.IGNORECASE,
)


def fixed_target_color_template(text):
    match = _INSTRUCTION.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    color = _COLORS.get(match["color"].casefold())
    effect = {"op": CHARACTERISTIC_OPERATION, "schema_version": FIXED_TARGET_COLOR_VERSION,
              "card": "$target.0", "colors": [color] if color else [],
              "duration": "until_end_of_turn" if match["temporary"] else "zone_object"}
    return ("fixed-spell-or-permanent-color-v1", (effect,), fixed_target_color_schema(),
            (FIXED_TARGET_COLOR_MECHANIC, "cr-611-continuous-effects", "cr-115-targets"))
