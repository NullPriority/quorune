"""Closed transport for a self-replacement on one unchanged creature target."""

from collections.abc import Mapping
from dataclasses import dataclass

from .conditional_target_preparation_model import preparation_results_are_closed
from .creature_subtypes import canonical_creature_subtype
from .object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from .object_query import ObjectQueryResult, object_matches_query
from .replacement.immutable import thaw_value

CURRENT_TARGET_REPLACEMENT_VERSION = 17
CURRENT_TARGET_REPLACEMENT_MECHANIC = "current-target-self-replacement"
CURRENT_TARGET_REPLACEMENT_CAPABILITY = "resolution.effect.current_target_self_replacement"


@dataclass(frozen=True, slots=True)
class TargetReplacementCondition:
    scope: str
    queries_any: tuple[ObjectQuerySpec, ...] = ()

    def __post_init__(self):
        queries = tuple(self.queries_any)
        if not ((self.scope == "night" and not queries)
                or (self.scope == "target" and queries and all(isinstance(query, ObjectQuerySpec) for query in queries))):
            raise ValueError("Replacement predicate requires current target traits or night")
        if self.scope == "target":
            allowed = False
            if len(queries) == 1:
                query = queries[0]
                allowed = (
                    len(query.subtypes_any) == 1
                    and canonical_creature_subtype(query.subtypes_any[0]) is not None
                    and query == ObjectQuerySpec(zones=("battlefield",), subtypes_any=query.subtypes_any)
                    or len(query.colors_any) == 1 and query.colors_any[0] in {"W", "U", "B", "R", "G"}
                    and query == ObjectQuerySpec(zones=("battlefield",), colors_any=query.colors_any)
                    or query == ObjectQuerySpec(zones=("battlefield",),
                        state_predicate=PermanentStatePredicateSpec(blocking=True)))
            else:
                allowed = queries == (
                    ObjectQuerySpec(zones=("battlefield",), types_all=("enchantment", "creature")),
                    ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), supertypes_all=("legendary",)))
            if not allowed:
                raise ValueError("Replacement target predicate is outside the supported characteristic grammar")
        object.__setattr__(self, "queries_any", queries)

    def to_dict(self):
        return {"kind": "current_target_replacement_condition", "schema_version": 1,
                "scope": self.scope, "queries_any": [query.to_dict() for query in self.queries_any]}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"kind", "schema_version", "scope", "queries_any"}
                or value["kind"] != "current_target_replacement_condition"
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Current target replacement predicate fields are closed")
        if not isinstance(value["queries_any"], (list, tuple)):
            raise ValueError("Current target predicates require an array of typed queries")
        return cls(value["scope"], tuple(ObjectQuerySpec.from_dict(query) for query in value["queries_any"]))

    def matches(self, row: ObjectQueryResult) -> bool:
        if self.scope != "target" or not isinstance(row, ObjectQueryResult):
            raise ValueError("A current target predicate requires typed public characteristics")
        return any(object_matches_query(row, query) for query in self.queries_any)


def validate_current_target_replacement(effect):
    fields = {"op", "schema_version", "player", "condition", "condition_target", "target_binding",
              "effects", "mechanic_ids", "prefix_mechanic_ids", "otherwise_effects", "otherwise_mechanic_ids"}
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect["op"] != "apply_if_public_condition"
            or type(effect["schema_version"]) is not int
            or effect["schema_version"] != CURRENT_TARGET_REPLACEMENT_VERSION
            or type(effect["player"]) is not str or not effect["player"]
            or not isinstance(effect["prefix_mechanic_ids"], (list, tuple))
            or effect["prefix_mechanic_ids"]):
        raise ValueError("Current target replacement has a closed versioned instruction")
    subject = effect["condition_target"]
    if subject is not None and (type(subject) is not str or not subject
            or subject.startswith("$") and subject != "$target.0"):
        raise ValueError("Current target replacement retains the original target")
    binding = thaw_value(effect["target_binding"])
    if (binding != {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}
            or type(binding.get("count")) is not int):
        raise ValueError("Current target replacement retains one unrestricted creature target")
    for branch, key in (("effects", "mechanic_ids"), ("otherwise_effects", "otherwise_mechanic_ids")):
        ids = effect[key]
        if (not isinstance(ids, (list, tuple)) or not ids
                or any(type(value) is not str or not value for value in ids)
                or len(set(ids)) != len(ids) or "fixed-resolution-public-condition" in ids
                or not preparation_results_are_closed(effect[branch], subject=subject)):
            raise ValueError("Both replacement branches require fixed results and declared owners")
    return TargetReplacementCondition.from_dict(effect["condition"]), tuple(effect["effects"])
