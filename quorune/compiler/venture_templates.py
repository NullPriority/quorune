from __future__ import annotations

"""Lower the two fixed-controller Venture entry modes."""

from dataclasses import dataclass
import re

from ..dungeon_catalog import VentureMode
from ..dungeon_progress import DUNGEON_VENTURE_FIELD, DUNGEON_VENTURE_MECHANIC
from ..initiative_model import TAKE_INITIATIVE_FIELD, TAKE_INITIATIVE_MECHANIC
from ..semantic_runtime.venture_limit import VENTURE_LIMIT_CAPABILITY, VENTURE_LIMIT_HANDLER


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


def fixed_venture_limit_template(text: str):
    if text.strip().casefold().removesuffix(".") != "each opponent can't venture into the dungeon more than once each turn":
        return None
    descriptor = {"handler_id": VENTURE_LIMIT_HANDLER, "schema_version": 1, "event": "characteristics.evaluate"}
    return "fixed-opponent-venture-limit-v1", descriptor, VENTURE_LIMIT_CAPABILITY


@dataclass(frozen=True, slots=True)
class FixedInitiativeTemplate:
    def compiled(self):
        effect = {"op": "choose_option", "player": "$controller", TAKE_INITIATIVE_FIELD: {"schema_version": 1}}
        return "fixed-controller-takes-initiative-v1", (effect,), None, (TAKE_INITIATIVE_MECHANIC,)


def fixed_initiative_effect_template(text: str) -> FixedInitiativeTemplate | None:
    if re.fullmatch(r"(?:you )?take the initiative\.?", text.strip(), re.IGNORECASE):
        return FixedInitiativeTemplate()
    return None
