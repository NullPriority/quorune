"""Closed current library-card characteristics without weakening old selectors."""

from collections.abc import Mapping
from dataclasses import dataclass

from .object_predicate import ObjectQuerySpec, object_query_capabilities
from .target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison
from .library_search_model import FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID

QUALIFIED_LIBRARY_SEARCH_CAPABILITY = "library.search.qualified_card"
CREATURE_KEYWORD_TOP_SEARCH_CAPABILITY = "library.search.creature_keyword_top"
CREATURE_KEYWORD_TOP_SEARCH_KEYWORDS = ("deathtouch", "hexproof", "reach", "trample")
QUALIFIED_LIBRARY_SEARCH_MECHANIC = "qualified-library-card-search"
QUALIFIED_LIBRARY_SEARCH_VERSION = 7
QUALIFIED_LIBRARY_SEARCH_PREDICATE = "qualified_library_query"
CREATURE_STAT_QUALIFICATION_VERSION = 2
CREATURE_STAT_LIBRARY_SEARCH_CAPABILITY = "library.search.creature_stat_bound"
_LIBRARY_CREATURE_QUERY = ObjectQuerySpec(zones=("library",), types_all=("creature",))
_TYPES = frozenset({"artifact", "battle", "creature", "enchantment", "land", "planeswalker"})


@dataclass(frozen=True, slots=True)
class QualifiedLibrarySearchSpec:
    query: ObjectQuerySpec
    destination: str
    reveal: bool
    mana_value_max: int | None = None
    mana_values_any: tuple[int, ...] = ()
    stat_bound: TargetNumericCharacteristicSpec | None = None
    keywords_any: tuple[str, ...] = ()

    def __post_init__(self):
        q = self.query
        if not isinstance(q, ObjectQuerySpec):raise ValueError("Qualified search requires a typed query")
        keywords = tuple(self.keywords_any)
        object.__setattr__(self, "keywords_any", keywords)
        if self.stat_bound is not None:
            if (
                not isinstance(self.stat_bound, TargetNumericCharacteristicSpec)
                or self.stat_bound.characteristic not in (
                    TargetNumericCharacteristic.POWER, TargetNumericCharacteristic.TOUGHNESS,
                )
                or self.stat_bound.comparison is not TargetNumericComparison.AT_MOST
                or self.stat_bound.value != 2
                or q != _LIBRARY_CREATURE_QUERY
                or self.destination != "hand"
                or self.reveal is not True
                or self.mana_value_max is not None
                or self.mana_values_any
                or keywords
            ):
                raise ValueError("Creature stat search requires its exact revealed hand result and inclusive bound")
            object.__setattr__(self, "mana_values_any", ())
            return
        if (not (q.types_all or q.subtypes_any) or set(q.types_all) - _TYPES
                or set(q.subtypes_any) - {"aura", "shrine"} or len(q.keywords_all) > 1
                or q.keywords_all not in ((), ("defender",))
                or q.excluded_supertypes not in ((), ("legendary",))
                or q != ObjectQuerySpec(zones=("library",), types_all=q.types_all,
                    subtypes_any=q.subtypes_any, colors_any=q.colors_any, keywords_all=q.keywords_all,
                    excluded_supertypes=q.excluded_supertypes)):
            raise ValueError("Qualified search is outside the closed library-card grammar")
        if keywords:
            if (keywords != CREATURE_KEYWORD_TOP_SEARCH_KEYWORDS or q != _LIBRARY_CREATURE_QUERY
                    or self.destination != "library_top" or self.reveal is not True
                    or self.mana_value_max is not None or self.mana_values_any):
                raise ValueError("Keyword-top search requires the exact creature, reveal and top result")
        elif self.destination not in {"hand", "battlefield"} or type(self.reveal) is not bool:
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
        if self.stat_bound is not None:
            return {
                "schema_version": CREATURE_STAT_QUALIFICATION_VERSION,
                "query": self.query.to_dict(),
                "stat_bound": self.stat_bound.to_dict(),
            }
        if self.keywords_any:
            return {"schema_version": 2, "query": self.query.to_dict(), "keywords_any": list(self.keywords_any)}
        return {"schema_version": 1, "query": self.query.to_dict(), "mana_value_max": self.mana_value_max,
                "mana_values_any": list(self.mana_values_any)}

    def effect(self):
        effect = {"op": "search", "schema_version": QUALIFIED_LIBRARY_SEARCH_VERSION, "zone": "library",
                  "selector": {"predicate": QUALIFIED_LIBRARY_SEARCH_PREDICATE},
                  "count": {"minimum": 1, "maximum": 1}, "destination": self.destination,
                  "reveal": self.reveal, "shuffle_after": not self.keywords_any,
                  "qualification": self.qualification()}
        if self.keywords_any:
            effect["shuffle_before_placement"] = True
        return effect

    @classmethod
    def from_effect(cls, effect):
        fields = {"op", "schema_version", "zone", "selector", "count", "destination", "reveal", "shuffle_after", "qualification"}
        if not isinstance(effect, Mapping) or set(effect) not in (fields, fields | {"shuffle_before_placement"}):
            raise ValueError("Qualified library search has a closed version 7 transport")
        if (effect["op"] != "search"
                or type(effect["schema_version"]) is not int or effect["schema_version"] != QUALIFIED_LIBRARY_SEARCH_VERSION
                or effect["zone"] != "library" or effect["selector"] != {"predicate": QUALIFIED_LIBRARY_SEARCH_PREDICATE}
                or not isinstance(effect["count"], Mapping) or effect["count"] != {"minimum": 1, "maximum": 1}
                or any(type(effect["count"][key]) is not int for key in ("minimum", "maximum"))):
            raise ValueError("Qualified library search has a closed version 7 transport")
        q = effect["qualification"]
        if not isinstance(q, Mapping) or type(q.get("schema_version")) is not int:
            raise ValueError("Qualified query requires a typed qualification version")
        if q["schema_version"] == CREATURE_STAT_QUALIFICATION_VERSION:
            if set(q) == {"schema_version", "query", "stat_bound"}:
                if set(effect) != fields or effect["shuffle_after"] is not True:
                    raise ValueError("Creature stat search requires a revealed hand result and then a shuffle")
                return cls(
                    ObjectQuerySpec.from_dict(q["query"]), effect["destination"], effect["reveal"],
                    stat_bound=TargetNumericCharacteristicSpec.from_mapping(q["stat_bound"]),
                )
            if (set(q) == {"schema_version", "query", "keywords_any"}
                    and isinstance(q["keywords_any"], (list, tuple))
                    and tuple(q["keywords_any"]) == CREATURE_KEYWORD_TOP_SEARCH_KEYWORDS
                    and effect.get("shuffle_before_placement") is True and effect["shuffle_after"] is False):
                return cls(
                    ObjectQuerySpec.from_dict(q["query"]), effect["destination"], effect["reveal"],
                    keywords_any=tuple(q["keywords_any"]),
                )
            raise ValueError("Version 2 requires the exact stat or keyword qualification and its shuffle sequence")
        if (q["schema_version"] != 1 or set(effect) != fields or effect["shuffle_after"] is not True
                or set(q) != {"schema_version", "query", "mana_value_max", "mana_values_any"}
                or not isinstance(q["mana_values_any"], (list, tuple))):
            raise ValueError("Qualified query fields are incomplete or unknown")
        return cls(ObjectQuerySpec.from_dict(q["query"]), effect["destination"], effect["reveal"],
                   q["mana_value_max"], tuple(q["mana_values_any"]))

    def capabilities(self):
        return (
            QUALIFIED_LIBRARY_SEARCH_CAPABILITY, FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
            *object_query_capabilities(self.query),
            *((CREATURE_KEYWORD_TOP_SEARCH_CAPABILITY,) if self.keywords_any else ()),
            *((CREATURE_STAT_LIBRARY_SEARCH_CAPABILITY,) if self.stat_bound is not None else ()),
        )

    def compiled(self):
        if self.keywords_any:
            template = "creature-keyword-library-top-v1"
        elif self.stat_bound is not None:
            template = "creature-stat-library-card-search-v2"
        else:
            template = "qualified-library-card-search-v1"
        return template, (self.effect(),), None, (
            FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID, QUALIFIED_LIBRARY_SEARCH_MECHANIC)
