"""Immutable fixed search data, shared by compilation and runtime validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap, thaw_value

FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID = "fixed-counted-library-search"
FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID = "library.search.fixed_counted"
_PERMANENT_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)
_CARD_TYPES = _PERMANENT_TYPES | {"instant", "kindred", "sorcery"}


def search_selector(query: ObjectQuerySpec) -> dict[str, list[str]]:
    fields = {
        "types": query.types_all,
        "types_any": query.types_any,
        "subtypes_any": query.subtypes_any,
        "supertypes": query.supertypes_all,
        "colors_any": query.colors_any,
    }
    return {name: list(values) for name, values in fields.items() if values}


def counted_search_selector_is_closed(
    selector: Mapping[str, Any], *, destination: str,
) -> bool:
    """Validate the fixed query data shared with the existing search owner."""
    if not isinstance(selector, Mapping) or set(selector) - {
        "types", "types_any", "subtypes_any", "supertypes", "colors_any", "names", "mana_value",
    }:
        return False
    ordinary = {
        key: value for key, value in selector.items()
        if key not in {"names", "mana_value"}
    }
    if any(
        not isinstance(value, (list, tuple)) or not value
        or any(type(item) is not str or not item for item in value)
        for value in ordinary.values()
    ):
        return False
    try:
        query = ObjectQuerySpec(
            types_all=tuple(ordinary.get("types", ())),
            types_any=tuple(ordinary.get("types_any", ())),
            subtypes_any=tuple(ordinary.get("subtypes_any", ())),
            supertypes_all=tuple(ordinary.get("supertypes", ())),
            colors_any=tuple(ordinary.get("colors_any", ())),
        )
    except (TypeError, ValueError):
        return False
    if search_selector(query) != {key: list(value) for key, value in ordinary.items()}:
        return False
    types = set((*query.types_all, *query.types_any))
    if types - _CARD_TYPES:
        return False
    if destination == "battlefield" and (not types or types - _PERMANENT_TYPES):
        return False
    if "names" in selector:
        names = selector["names"]
        if (
            not isinstance(names, (list, tuple)) or len(names) != 1
            or type(names[0]) is not str or not names[0].strip()
            or "\n" in names[0]
        ):
            return False
    if "mana_value" in selector:
        constraint = selector["mana_value"]
        if (
            not isinstance(constraint, Mapping)
            or set(constraint) not in ({"equal"}, {"minimum"}, {"maximum"})
            or any(type(value) is not int or value < 0 for value in constraint.values())
        ):
            return False
    return True


@dataclass(frozen=True, slots=True)
class FixedCountedLibrarySearchTemplate:
    count: int
    optional_count: bool
    selector: FrozenMap
    destination: str
    reveal: bool
    enters_tapped: bool = False

    def __post_init__(self) -> None:
        if type(self.count) is not int or not 1 <= self.count <= 10:
            raise ValueError("Fixed search count must be between one and ten")
        if any(
            type(value) is not bool
            for value in (self.optional_count, self.reveal, self.enters_tapped)
        ):
            raise ValueError("Fixed search flags must be booleans")
        if (
            type(self.destination) is not str
            or self.destination not in {"hand", "battlefield", "graveyard", "library_top"}
            or self.count > 1 and self.destination != "hand"
        ):
            raise ValueError("Fixed search destination or cardinality is unsupported")
        if self.enters_tapped and self.destination != "battlefield":
            raise ValueError("Only battlefield search results may enter tapped")
        if not counted_search_selector_is_closed(self.selector, destination=self.destination):
            raise ValueError("Fixed search selector is outside the closed vocabulary")
        object.__setattr__(self, "selector", FrozenMap(self.selector))

    def effect(self) -> dict[str, Any]:
        value = {
            "op": "search",
            "schema_version": 2,
            "zone": "library",
            "selector": thaw_value(self.selector),
            "count": {"minimum": 0 if self.optional_count else self.count, "maximum": self.count},
            "destination": self.destination,
            "reveal": self.reveal,
            "shuffle_after": self.destination != "library_top",
            "shuffle_before_placement": self.destination == "library_top",
        }
        if self.enters_tapped:
            value["enters_tapped_override"] = True
        return value

    def compiled(self) -> tuple[str, tuple[dict[str, Any], ...], None, tuple[str, ...]]:
        return (
            "fixed-counted-library-search-v2", (self.effect(),), None,
            (FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,),
        )

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any]) -> "FixedCountedLibrarySearchTemplate":
        fields = {
            "op", "schema_version", "zone", "selector", "count", "destination",
            "reveal", "shuffle_after", "shuffle_before_placement",
        }
        if (
            not isinstance(effect, Mapping)
            or set(effect) not in (fields, fields | {"enters_tapped_override"})
            or type(effect.get("schema_version")) is not int
            or effect["schema_version"] != 2
        ):
            raise ValueError("Counted search instruction fields are incomplete or unknown")
        count = effect["count"]
        if (
            not isinstance(count, Mapping) or set(count) != {"minimum", "maximum"}
            or type(count["minimum"]) is not int or type(count["maximum"]) is not int
            or count["minimum"] not in {0, count["maximum"]}
        ):
            raise ValueError("Counted search bounds are malformed")
        if not isinstance(effect["selector"], Mapping):
            raise ValueError("Counted search selector must be a mapping")
        value = cls(
            count=count["maximum"], optional_count=count["minimum"] == 0,
            selector=FrozenMap(effect["selector"]), destination=effect["destination"],
            reveal=effect["reveal"],
            enters_tapped=effect.get("enters_tapped_override", False),
        )
        if value.effect() != thaw_value(effect):
            raise ValueError("Counted search instruction contradicts its typed contract")
        return value
