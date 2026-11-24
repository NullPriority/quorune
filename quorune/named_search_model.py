"""Immutable named search: select zones before exposing any hidden results."""

from collections.abc import Mapping
from dataclasses import dataclass

from .replacement.immutable import FrozenMap

LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION = 11
LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION = 13
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


@dataclass(frozen=True, slots=True)
class LiteralNameAnyNumberSearchSpec:
    """One explicit name with a bound derived from the legal private search window."""

    name: str

    def __post_init__(self):
        if (type(self.name) is not str or not self.name or self.name != self.name.strip()
                or len(self.name) > 200 or "\n" in self.name or "\r" in self.name
                or self.name.startswith("$")):
            raise ValueError("An any-number named search requires one literal card name")

    def effect(self):
        return {"op": "search", "schema_version": LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, "zone": "library",
                "selector": {"names": [self.name]}, "count": {"minimum": 0, "maximum": "all_eligible"},
                "destination": "hand", "reveal": True, "shuffle_after": True}

    @classmethod
    def from_effect(cls, effect):
        if not isinstance(effect, Mapping):
            raise ValueError("An any-number named search requires its typed instruction")
        selector = effect.get("selector")
        names = selector.get("names") if isinstance(selector, Mapping) else None
        if not isinstance(names, (list, tuple)) or len(names) != 1:
            raise ValueError("An any-number named search requires one literal card name")
        spec = cls(names[0])
        expected = spec.effect()
        expected["selector"]["names"] = type(names)(expected["selector"]["names"])
        if (dict(effect) != expected or type(effect["schema_version"]) is not int
                or type(effect["count"]["minimum"]) is not int
                or effect["reveal"] is not True or effect["shuffle_after"] is not True):
            raise ValueError("An any-number named search requires its exact private count and revealed hand result")
        return spec


@dataclass(frozen=True, slots=True)
class LiteralNameBattlefieldSearchSpec:
    """Find at most one permanent with one of one or two printed names."""

    names: tuple[str, ...]
    enters_tapped: bool = False

    def __post_init__(self):
        if not isinstance(self.names, tuple) or not 1 <= len(self.names) <= 2:
            raise ValueError("Named battlefield search requires one or two printed names")
        for name in self.names:
            LiteralNameAnyNumberSearchSpec(name)
        if len({name.casefold() for name in self.names}) != len(self.names):
            raise ValueError("Named battlefield search requires distinct names")
        if type(self.enters_tapped) is not bool:
            raise ValueError("Named battlefield search requires an exact entry orientation")

    def effect(self):
        fields = {
            "op": "search", "schema_version": LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION,
            "zone": "library", "selector": {"names": list(self.names)},
            "count": {"minimum": 0, "maximum": 1}, "destination": "battlefield",
            "shuffle_after": True,
        }
        if self.enters_tapped:
            fields["enters_tapped_override"] = True
        return fields

    def compiled(self):
        from .library_search_model import FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID

        return ("literal-name-library-battlefield-search-v1", (self.effect(),), None,
                (FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,))

    @classmethod
    def from_effect(cls, effect):
        if not isinstance(effect, Mapping):
            raise ValueError("Named battlefield search requires its typed instruction")
        selector = effect.get("selector")
        names = selector.get("names") if isinstance(selector, Mapping) else None
        if not isinstance(names, (tuple, list)):
            raise ValueError("Named battlefield search needs fixed literal names")
        spec = cls(tuple(names), effect.get("enters_tapped_override", False))
        if (FrozenMap(effect) != FrozenMap(spec.effect())
                or type(effect["schema_version"]) is not int
                or any(type(value) is not int for value in effect["count"].values())
                or effect["shuffle_after"] is not True):
            raise ValueError("Named battlefield search requires its original count, destination and shuffle")
        return spec


def decode_literal_library_search(effect):
    if effect.get("schema_version") == LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION:
        return LiteralNameBattlefieldSearchSpec.from_effect(effect)
    return LiteralNameAnyNumberSearchSpec.from_effect(effect)


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
