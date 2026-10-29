"""Closed fixed land choices made while an untap instruction resolves."""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

UNTARGETED_UNTAP_OPERATION = 'choose_untap_lands'
UNTARGETED_UNTAP_CAPABILITY = 'permanent.untap.fixed_land_choice'
UNTARGETED_UNTAP_MECHANIC = 'fixed-untargeted-land-untap'


@dataclass(frozen=True, slots=True)
class FixedLandUntapChoiceSpec:
    maximum: int
    controller_scope: str = 'any'

    def __post_init__(self) -> None:
        if type(self.maximum) is not int or not 1 <= self.maximum <= 10:
            raise ValueError('Land untap choice requires a fixed positive maximum')
        if type(self.controller_scope) is not str or self.controller_scope not in {'any', 'controller'}:
            raise ValueError('Land untap controller scope is closed')

    def effect(self, player: str = '$controller') -> dict[str, Any]:
        return {
            'op': UNTARGETED_UNTAP_OPERATION, 'player': player,
            'maximum': self.maximum, 'controller_scope': self.controller_scope,
        }

    @classmethod
    def from_effect(cls, value: Mapping[str, Any]) -> 'FixedLandUntapChoiceSpec':
        if (
            not isinstance(value, Mapping)
            or set(value) != {'op', 'player', 'maximum', 'controller_scope'}
            or value['op'] != UNTARGETED_UNTAP_OPERATION
            or type(value['player']) is not str or not value['player']
        ):
            raise ValueError('Land untap choice has a closed input schema')
        return cls(value['maximum'], value['controller_scope'])
