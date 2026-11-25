"""One selected logical creature with a committed departure-stat life result."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..power_sacrifice_model import GreatestPowerSacrificeLifeResult
from ..zone_trigger_events import ZoneTransitionKind
from .intents import MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True, slots=True)
class TargetedPowerSacrificeLifeIntent(MoveObjectsSimultaneouslyIntent):
    result: GreatestPowerSacrificeLifeResult | None = None

    def __post_init__(self) -> None:
        MoveObjectsSimultaneouslyIntent.__post_init__(self)
        if (
            len(self.object_refs) != 1 or self.expected_zones != ("battlefield",)
            or self.destination != "graveyard" or self.owned_only or self.controlled_only
            or self.transition_kind is not ZoneTransitionKind.SACRIFICE
            or self.battlefield_controller is not None or self.battlefield_tapped or self.library_order is not None
            or not isinstance(self.result, GreatestPowerSacrificeLifeResult)
            or ((self.result.characteristic == "toughness" or self.result.draw_after_life or self.result.draw_then_lose_life)
                and self.result.player != self.actor)
        ):
            raise ValueError("Sacrifice life requires one exact affected player's selected creature")

    def identity(self) -> dict:
        return {
            "actor": self.actor, "card_ref": self.object_refs[0],
            "result": self.result.to_dict(), "reason": self.reason,
        }


def validate_targeted_power_sacrifice_life_identity(value: Mapping) -> dict:
    strings = {"actor", "card_ref", "reason"}
    if (not isinstance(value, Mapping) or set(value) != strings | {"result"}
            or any(type(value[key]) is not str or not value[key] for key in strings)):
        raise ValueError("Sacrifice life continuation requires its exact movement identity")
    result = GreatestPowerSacrificeLifeResult.from_dict(value["result"])
    if (result.characteristic == "toughness" or result.draw_after_life or result.draw_then_lose_life) and result.player != value["actor"]:
        raise ValueError("Controller sacrifice life requires the original actor")
    return {**dict(value), "result": result.to_dict()}
