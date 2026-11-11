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
AFFECTED_HAND_TOP_CAPABILITY = "library.order.affected_player_hand_to_top"
AFFECTED_HAND_TOP_MECHANIC = "fixed-affected-player-hand-to-top"
HAND_TOP_APNAP_RESULT = "library_top"
HAND_TOP_APNAP_EVENT = "choice.library_top"
HAND_TOP_FIRST = "top_to_bottom"
HAND_TOP_ORDER_RULES = ("400.7", "401.4", "608.2c", "608.2d", "609.3")
GRAVEYARD_LIBRARY_ORDER_OPERATION = "put_graveyard_targets_in_library"
GRAVEYARD_LIBRARY_ORDER_CAPABILITY = "library.order.graveyard_targets"
GRAVEYARD_LIBRARY_ORDER_MECHANIC = "ordered-graveyard-library-targets"
GRAVEYARD_LIBRARY_ORDER_RULES = ("115.3", "400.7", "401.4", "608.2b", "608.2c")
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


def is_affected_hand_top_choice(effect: Mapping[str, Any]) -> bool:
    return effect.get("then") == HAND_TOP_APNAP_RESULT


def _library_order_members(value: Any) -> tuple[str, ...]:
    if (not isinstance(value, (list, tuple))
            or any(type(member) is not str or not member for member in value)
            or len(set(value)) != len(value)):
        raise ValueError("Library arrival order requires distinct members")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class LibraryArrivalOrder:
    """Private edge order over stable batch member identifiers."""

    chooser: str
    position: str
    members: tuple[str, ...]

    def __post_init__(self) -> None:
        members = _library_order_members(self.members)
        if type(self.chooser) is not str or not self.chooser or self.position not in ("top", "bottom") or not members:
            raise ValueError("Library arrival order requires a chooser, an edge and current members")
        object.__setattr__(self, "members", members)

    def to_dict(self) -> dict[str, Any]:
        return {"chooser": self.chooser, "position": self.position, "members": list(self.members)}

    @property
    def origin_zones(self) -> tuple[str, ...]:
        return ("graveyard",)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LibraryArrivalOrder":
        if isinstance(value, Mapping) and set(value) == {"chooser", "position", "members", "scope"}:
            if value["scope"] == "event_departure":
                return DepartedLibraryArrivalOrder(value["chooser"], value["position"], value["members"])
        if not isinstance(value, Mapping) or set(value) != {"chooser", "position", "members"}:
            raise ValueError("Library arrival order has unknown or missing fields")
        return cls(value["chooser"], value["position"], value["members"])


@dataclass(frozen=True, slots=True)
class DepartedLibraryArrivalOrder(LibraryArrivalOrder):
    """The owner's private order for a surviving public departure group."""

    position: str | int

    def __post_init__(self) -> None:
        members = _library_order_members(self.members)
        if (type(self.chooser) is not str or not self.chooser or len(members) < 2
                or type(self.position) not in (str, int)
                or self.position not in ("top", "bottom", 2, 3, 4, 5)):
            raise ValueError("Departed library order requires a private owner and fixed group position")
        object.__setattr__(self, "members", members)

    @property
    def origin_zones(self) -> tuple[str, ...]:
        return ("graveyard", "exile")

    def to_dict(self) -> dict[str, Any]:
        return {**LibraryArrivalOrder.to_dict(self), "scope": "event_departure"}


def validate_graveyard_library_order_effect(value: Mapping[str, Any], *, resolved: bool = False) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"op", "cards", "position", "scope"}
        or value["op"] != GRAVEYARD_LIBRARY_ORDER_OPERATION
        or value["position"] not in ("top", "bottom")
        or value["scope"] not in ("own", "opponent")
    ):
        raise ValueError("Graveyard library order fields are closed")
    cards = value["cards"]
    if resolved:
        _library_order_members(cards)
    elif cards != "$targets":
        raise ValueError("Graveyard library order must use announced targets")


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
