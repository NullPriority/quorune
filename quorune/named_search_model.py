"""Immutable named search: select zones before exposing any hidden results."""

from collections.abc import Mapping
from dataclasses import dataclass

NAMED_ZONE_SEARCH_OPERATION = "search_named_library_or_graveyard"
NAMED_ZONE_SEARCH_MECHANIC = "named-library-graveyard-search"
NAMED_ZONE_SEARCH_CAPABILITY = "library.search.named_library_graveyard"
NAMED_ZONE_SEARCH_RULES = ("400.2", "401.2", "404.1", "608.2b", "608.2c", "608.2d",
                           "701.20a", "701.23a", "701.23b", "701.23e", "701.23f", "701.24a")


@dataclass(frozen=True, slots=True)
class NamedZoneSearchSpec:
    name: str
    optional: bool

    def __post_init__(self):
        if (type(self.name) is not str or not self.name or self.name != self.name.strip()
                or len(self.name) > 200 or "\n" in self.name or "\r" in self.name
                or self.name.startswith("$") or type(self.optional) is not bool):
            raise ValueError("Named zone search requires one fixed name and explicit optionality")

    def to_dict(self):
        return {"schema_version": 1, "name": self.name, "optional": self.optional}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "name", "optional"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Named zone search has an explicit closed codec")
        return cls(value["name"], value["optional"])

    def instruction(self, player="$controller"):
        return {"op": NAMED_ZONE_SEARCH_OPERATION, "player": player, "search": self.to_dict()}

    def search_instruction(self, choice):
        if type(choice) is not str or choice not in {"library", "graveyard", "both"}:
            raise ValueError("Named search requires one legal searched-zone choice")
        zones = ["library", "graveyard"] if choice == "both" else [choice]
        return {"op": "search", "schema_version": 6, "zone": zones, "selector": {"names": [self.name]},
                "count": {"minimum": 1, "maximum": 1}, "destination": "hand", "reveal": True,
                "shuffle_after": "library" in zones, "optional": False}


def decode_named_zone_search(effect, *, actor=None):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", "search"}
            or effect["op"] != NAMED_ZONE_SEARCH_OPERATION
            or effect["player"] != (actor if actor is not None else "$controller")):
        raise ValueError("Named zone search retains the original resolving controller")
    return NamedZoneSearchSpec.from_dict(effect["search"])


def decode_named_zone_selection(effect):
    fields = {"op", "schema_version", "zone", "selector", "count", "destination", "reveal", "shuffle_after", "optional"}
    if (not isinstance(effect, Mapping) or set(effect) != fields or effect["op"] != "search"
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 6
            or not isinstance(effect["zone"], (list, tuple))):
        raise ValueError("Named search selection requires its closed version6 payload")
    zones = tuple(effect["zone"])
    if (zones not in (("library",), ("graveyard",), ("library", "graveyard"))
            or effect["destination"] != "hand" or effect["reveal"] is not True
            or effect["optional"] is not False or type(effect["shuffle_after"]) is not bool
            or effect["shuffle_after"] != ("library" in zones)):
        raise ValueError("Named search selection changed its searched zones or result")
    count = effect["count"];selector = effect["selector"]
    if (not isinstance(count, Mapping) or count != {"minimum": 1, "maximum": 1}
            or any(type(count[key]) is not int for key in ("minimum", "maximum"))
            or not isinstance(selector, Mapping) or set(selector) != {"names"}
            or not isinstance(selector["names"], (list, tuple)) or len(selector["names"]) != 1):
        raise ValueError("Named search selection retains exactly one stated card name")
    return NamedZoneSearchSpec(selector["names"][0], False), zones
