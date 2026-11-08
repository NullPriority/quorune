"""Closed controller library ordering and shuffle instructions."""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

TOP_ORDER_OPERATION = 'order_library_top'
TOP_ORDER_CAPABILITY = 'library.order.fixed_controller'
TOP_ORDER_MECHANIC = 'fixed-controller-top-order'
HAND_TOP_ORDER_OPERATION = "put_hand_cards_on_library_top"
HAND_TOP_ORDER_CAPABILITY = "library.order.fixed_hand_to_top"
HAND_TOP_ORDER_MECHANIC = "fixed-hand-to-library-top"
HAND_TOP_ORDER_RULES = ("400.7", "401.4", "608.2c", "608.2d", "609.3")
SHUFFLE_OPERATION = 'shuffle_controller_library'
SHUFFLE_CAPABILITY = 'library.shuffle.fixed_controller'
SHUFFLE_MECHANIC = 'fixed-controller-shuffle'


@dataclass(frozen=True, slots=True)
class FixedTopLibraryOrderSpec:
    count: int

    def __post_init__(self) -> None:
        if type(self.count) is not int or not 1 <= self.count <= 10:
            raise ValueError('Fixed top order needs a represented positive count')

    def effect(self, player: str = '$controller') -> dict[str, Any]:
        return {'op': TOP_ORDER_OPERATION, 'player': player, 'count': self.count}

    @classmethod
    def from_effect(cls, value: Mapping[str, Any]) -> 'FixedTopLibraryOrderSpec':
        if (
            not isinstance(value, Mapping)
            or set(value) != {'op', 'player', 'count'}
            or value['op'] != TOP_ORDER_OPERATION
            or type(value['player']) is not str
            or not value['player']
        ):
            raise ValueError('Fixed top order fields are closed')
        return cls(value['count'])


@dataclass(frozen=True, slots=True)
class FixedHandTopOrderSpec:
    count: int

    def __post_init__(self) -> None:
        if type(self.count) is not int or not 1 <= self.count <= 10:
            raise ValueError("Hand-to-top order requires a represented positive count")

    def effect(self, player: str = "$controller") -> dict[str, Any]:
        return {"op": HAND_TOP_ORDER_OPERATION, "player": player, "count": self.count}

    @classmethod
    def from_effect(cls, value: Mapping[str, Any]) -> "FixedHandTopOrderSpec":
        if (
            not isinstance(value, Mapping)
            or set(value) != {"op", "player", "count"}
            or value["op"] != HAND_TOP_ORDER_OPERATION
            or type(value["player"]) is not str
            or not value["player"]
        ):
            raise ValueError("Hand-to-top order fields are closed")
        return cls(value["count"])


def fixed_shuffle_effect(player: str = '$controller') -> dict[str, str]:
    return {'op': SHUFFLE_OPERATION, 'player': player}


def validate_fixed_shuffle_effect(value: Mapping[str, Any]) -> str:
    if (
        not isinstance(value, Mapping)
        or set(value) != {'op', 'player'}
        or value['op'] != SHUFFLE_OPERATION
        or type(value['player']) is not str
        or not value['player']
    ):
        raise ValueError('Fixed shuffle needs one represented player')
    return value['player']
