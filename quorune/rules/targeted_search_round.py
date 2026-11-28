"""Closed authority and collected choices for a targeted private search round."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import GameRuleError
from ..library_search_model import FixedCountedLibrarySearchTemplate
from ..replacement.immutable import FrozenMap, thaw_value
from .library_selection import LibrarySelectionObjectIdentity


CAPABILITY = "library.search.target_pair_private_top"
MECHANIC = "target-pair-private-library-search"
ROUND_FIELD = "targeted_search_round"


def target_pair_search_effect():
    return {"op": "search", ROUND_FIELD: 1, "targeted_players": "$targets"}


def target_pair_search_schema():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 2}


def one_private_library_search():
    return FixedCountedLibrarySearchTemplate(1, False, FrozenMap({}), "library_top", False).effect()


def target_pair_search_payload_dependencies(effect):
    if effect.get("op") != "search" or ROUND_FIELD not in effect:
        return ()
    if type(effect[ROUND_FIELD]) is not int or thaw_value(effect) != target_pair_search_effect():
        raise ValueError("Targeted library search requires its complete printed instruction")
    return (CAPABILITY, "target.revalidate_resolution")


def target_pair_search_node_capabilities(*, effects, target_schema, mechanic_ids):
    if MECHANIC not in mechanic_ids or len(effects) != 1 or thaw_value(target_schema) != target_pair_search_schema():
        return ()
    try:
        return target_pair_search_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


@dataclass(frozen=True, slots=True)
class PrivateFind:
    seat: str
    choice_id: str | None
    card: LibrarySelectionObjectIdentity | None
    destination_controller: str | None = None

    def __post_init__(self):
        if (type(self.seat) is not str or not self.seat
                or (self.choice_id is not None and (type(self.choice_id) is not str or not self.choice_id))
                or (self.card is not None and (not isinstance(self.card, LibrarySelectionObjectIdentity) or self.choice_id is None))
                or (self.destination_controller is not None and (type(self.destination_controller) is not str
                     or self.destination_controller in ("", self.seat) or self.card is None))):
            raise GameRuleError("The private library find lost its original current choice")

    def to_dict(self):
        return {"seat": self.seat, "choice_id": self.choice_id, "card": self.card.to_dict() if self.card else None,
                "destination_controller": self.destination_controller}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"seat", "choice_id", "card", "destination_controller"}:
            raise GameRuleError("The private library find has unexpected fields")
        card = LibrarySelectionObjectIdentity.from_dict(value["card"]) if value["card"] is not None else None
        return cls(value["seat"], value["choice_id"], card, value["destination_controller"])


@dataclass(frozen=True, slots=True)
class TargetedSearchRound:
    seats: tuple[str, ...]
    findings: tuple[PrivateFind, ...] = ()

    def __post_init__(self):
        if (not 1 <= len(self.seats) <= 2 or any(type(seat) is not str or not seat for seat in self.seats)
                or len(set(self.seats)) != len(self.seats)
                or tuple(found.seat for found in self.findings) != self.seats[:len(self.findings)]
                or len(self.findings) > len(self.seats)):
            raise GameRuleError("The targeted search round lost its APNAP participants")

    @property
    def next_seat(self):
        return self.seats[len(self.findings)] if len(self.findings) < len(self.seats) else None

    def to_dict(self):
        return {"schema_version": 1, "seats": list(self.seats), "findings": [value.to_dict() for value in self.findings]}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "seats", "findings"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["seats"], (list, tuple)) or not isinstance(value["findings"], (list, tuple))):
            raise GameRuleError("The targeted search round has unexpected fields")
        return cls(tuple(value["seats"]), tuple(PrivateFind.from_dict(row) for row in value["findings"]))
