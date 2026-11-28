"""Closed fixed land choices made while an untap instruction resolves."""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

UNTARGETED_UNTAP_OPERATION = 'choose_untap_lands'
UNTARGETED_UNTAP_CAPABILITY = 'permanent.untap.fixed_land_choice'
UNTARGETED_UNTAP_MECHANIC = 'fixed-untargeted-land-untap'
SCHEDULED_LAND_UNTAP_VERSION = 2


def is_scheduled_land_untap(effect: Mapping[str, Any]) -> bool:
    return (effect.get("op") == UNTARGETED_UNTAP_OPERATION
            and type(effect.get("schema_version")) is int
            and effect["schema_version"] == SCHEDULED_LAND_UNTAP_VERSION)


@dataclass(frozen=True, slots=True)
class FixedLandUntapChoiceSpec:
    maximum: int
    controller_scope: str = 'any'
    schema_version: int = 1

    @property
    def scheduled(self) -> bool:
        return self.schema_version == SCHEDULED_LAND_UNTAP_VERSION

    def __post_init__(self) -> None:
        if type(self.maximum) is not int or not 1 <= self.maximum <= 10:
            raise ValueError('Land untap choice requires a fixed positive maximum')
        if type(self.controller_scope) is not str or self.controller_scope not in {'any', 'controller'}:
            raise ValueError('Land untap controller scope is closed')
        if (type(self.schema_version) is not int or self.schema_version not in (1, SCHEDULED_LAND_UNTAP_VERSION)
                or self.scheduled and (self.maximum != 1 or self.controller_scope != "controller")):
            raise ValueError("Scheduled untap requires exactly one land controlled by the participant")

    def effect(self, player: str = '$controller') -> dict[str, Any]:
        return {
            'op': UNTARGETED_UNTAP_OPERATION, 'player': player,
            'maximum': self.maximum, 'controller_scope': self.controller_scope,
            **({"schema_version": self.schema_version} if self.scheduled else {}),
        }

    @classmethod
    def from_effect(cls, value: Mapping[str, Any]) -> 'FixedLandUntapChoiceSpec':
        fields = {"op", "player", "maximum", "controller_scope"}
        scheduled = isinstance(value, Mapping) and is_scheduled_land_untap(value)
        if scheduled:
            fields.add("schema_version")
        if (not isinstance(value, Mapping) or set(value) != fields or value["op"] != UNTARGETED_UNTAP_OPERATION
                or type(value["player"]) is not str or not value["player"]):
            raise ValueError("Land untap choice has a closed input schema")
        return cls(value["maximum"], value["controller_scope"], SCHEDULED_LAND_UNTAP_VERSION if scheduled else 1)


def scheduled_land_untap_effect():
    return FixedLandUntapChoiceSpec(1, "controller", SCHEDULED_LAND_UNTAP_VERSION).effect("$context.player")
