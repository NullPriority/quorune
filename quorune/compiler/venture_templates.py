from __future__ import annotations

"""Lower the two fixed-controller Venture entry modes."""

from dataclasses import dataclass
import re

from ..dungeon_catalog import VentureMode
from ..dungeon_progress import DUNGEON_VENTURE_FIELD, DUNGEON_VENTURE_MECHANIC


@dataclass(frozen=True, slots=True)
class FixedVentureTemplate:
    mode: VentureMode

    def compiled(self):
        effect = {"op": "choose_option", "player": "$controller",
                  DUNGEON_VENTURE_FIELD: {"schema_version": 1, "mode": self.mode.value}}
        return "fixed-controller-venture-v1", (effect,), None, (DUNGEON_VENTURE_MECHANIC,)


def fixed_venture_effect_template(text: str) -> FixedVentureTemplate | None:
    match = re.fullmatch(r"venture into (?P<pool>the dungeon|Undercity)\.?", text.strip(), re.IGNORECASE)
    if match is None:
        return None
    return FixedVentureTemplate(VentureMode.ORDINARY if match["pool"].casefold() == "the dungeon" else VentureMode.UNDERCITY)
