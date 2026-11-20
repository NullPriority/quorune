"""Closed current library-card characteristics without weakening old selectors."""

from collections.abc import Mapping
from dataclasses import dataclass

from .object_predicate import ObjectQuerySpec, object_query_capabilities
from .library_search_model import FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID

QUALIFIED_LIBRARY_SEARCH_CAPABILITY = "library.search.qualified_card"
QUALIFIED_LIBRARY_SEARCH_MECHANIC = "qualified-library-card-search"
QUALIFIED_LIBRARY_SEARCH_VERSION = 7
QUALIFIED_LIBRARY_SEARCH_PREDICATE = "qualified_library_query"
_TYPES = frozenset({"artifact", "battle", "creature", "enchantment", "land", "planeswalker"})


@dataclass(frozen=True, slots=True)
class QualifiedLibrarySearchSpec:
    query: ObjectQuerySpec
    destination: str
    reveal: bool
    mana_value_max: int | None = None
    mana_values_any: tuple[int, ...] = ()

    def __post_init__(self):
        q = self.query
        if not isinstance(q, ObjectQuerySpec):raise ValueError("Qualified search requires a typed query")
        if (not (q.types_all or q.subtypes_any) or set(q.types_all) - _TYPES
                or set(q.subtypes_any) - {"aura", "shrine"} or len(q.keywords_all) > 1
                or q.keywords_all not in ((), ("defender",))
                or q.excluded_supertypes not in ((), ("legendary",))
                or q != ObjectQuerySpec(zones=("library",), types_all=q.types_all,
                    subtypes_any=q.subtypes_any, colors_any=q.colors_any, keywords_all=q.keywords_all,
                    excluded_supertypes=q.excluded_supertypes)):
            raise ValueError("Qualified search is outside the closed library-card grammar")
        if self.destination not in {"hand", "battlefield"} or type(self.reveal) is not bool:
            raise ValueError("Qualified search requires a fixed hand or battlefield result")
        if self.destination == "battlefield" and "creature" not in q.types_all:
            raise ValueError("Qualified battlefield entry requires a creature card")
        if self.mana_value_max is not None and (type(self.mana_value_max) is not int or self.mana_value_max < 0):
            raise ValueError("Qualified mana bound is a nonnegative fixed integer")
        values = tuple(self.mana_values_any)
        if values and (len(values) != 2 or any(type(value) is not int or value < 0 for value in values)
                or values != tuple(sorted(set(values))) or self.mana_value_max is not None):
            raise ValueError("Qualified mana alternatives are two distinct fixed values")
        object.__setattr__(self, "mana_values_any", values)

    def qualification(self):
        return {"schema_version": 1, "query": self.query.to_dict(), "mana_value_max": self.mana_value_max,
                "mana_values_any": list(self.mana_values_any)}

    def effect(self):
        return {"op": "search", "schema_version": QUALIFIED_LIBRARY_SEARCH_VERSION, "zone": "library",
                "selector": {"predicate": QUALIFIED_LIBRARY_SEARCH_PREDICATE},
                "count": {"minimum": 1, "maximum": 1}, "destination": self.destination,
                "reveal": self.reveal, "shuffle_after": True, "qualification": self.qualification()}

    @classmethod
    def from_effect(cls, effect):
        fields = {"op", "schema_version", "zone", "selector", "count", "destination", "reveal", "shuffle_after", "qualification"}
        if (not isinstance(effect, Mapping) or set(effect) != fields or effect["op"] != "search"
                or type(effect["schema_version"]) is not int or effect["schema_version"] != QUALIFIED_LIBRARY_SEARCH_VERSION
                or effect["zone"] != "library" or effect["selector"] != {"predicate": QUALIFIED_LIBRARY_SEARCH_PREDICATE}
                or not isinstance(effect["count"], Mapping) or effect["count"] != {"minimum": 1, "maximum": 1}
                or any(type(effect["count"][key]) is not int for key in ("minimum", "maximum"))
                or effect["shuffle_after"] is not True):
            raise ValueError("Qualified library search has a closed version7 transport")
        q = effect["qualification"]
        if (not isinstance(q, Mapping) or set(q) != {"schema_version", "query", "mana_value_max", "mana_values_any"}
                or type(q["schema_version"]) is not int or q["schema_version"] != 1
                or not isinstance(q["mana_values_any"], (list, tuple))):
            raise ValueError("Qualified query fields are incomplete or unknown")
        return cls(ObjectQuerySpec.from_dict(q["query"]), effect["destination"], effect["reveal"],
                   q["mana_value_max"], tuple(q["mana_values_any"]))

    def capabilities(self):
        return (QUALIFIED_LIBRARY_SEARCH_CAPABILITY, FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
                *object_query_capabilities(self.query))

    def compiled(self):
        return "qualified-library-card-search-v1", (self.effect(),), None, (
            FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID, QUALIFIED_LIBRARY_SEARCH_MECHANIC)
