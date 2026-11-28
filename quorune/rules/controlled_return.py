from __future__ import annotations

"""Closed descriptors for one bounded controller-selected permanent return."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..target_forms import SharedPermanentTypeScope

CONTROLLED_RETURN_FIELD = "controlled_return"
CONTROLLED_RETURN_CAPABILITY = "choice.controller.bounded_return_owner_hand"
CONTROLLED_RETURN_MECHANIC = "bounded-controlled-return"
SHARED_ENTRY_RETURN_CAPABILITY = "resolution.event_object.shared_permanent_type_return"
_RETURN_FIELDS = {"op", CONTROLLED_RETURN_FIELD}
_BASIC_LANDS = {"plains", "island", "swamp", "mountain", "forest"}


@dataclass(frozen=True, slots=True)
class ControlledReturn:
    predicate: ObjectQuerySpec
    minimum: int
    maximum: int
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.minimum) is not int or type(self.maximum) is not int
                or (self.minimum, self.maximum) not in {(0, 1), (0, 2), (0, 3), (1, 1)}):
            raise ValueError("Controlled return needs one fixed or bounded optional count")
        query = self.predicate
        if not isinstance(query, ObjectQuerySpec) or not query.controller:
            raise ValueError("Controlled return needs an authenticated controller predicate")
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise ValueError("Controlled return has an invalid version")
        if self.schema_version == 2:
            self._validate_shared_entry_query()
            return
        projected = ObjectQuerySpec(
            zones=("battlefield",), controller=query.controller, known_to_actor=True,
            exclude_ref=query.exclude_ref, types_all=query.types_all, excluded_types=query.excluded_types,
            subtypes_all=query.subtypes_all, excluded_subtypes=query.excluded_subtypes,
            colors_any=query.colors_any, tapped=query.tapped,
        )
        if (
            projected != query
            or query.types_all not in ((), ("artifact",), ("creature",), ("enchantment",), ("land",))
            or query.excluded_types not in ((), ("land",))
            or query.excluded_types and query.types_all
            or query.subtypes_all and (query.types_all != ("land",) or not set(query.subtypes_all) <= _BASIC_LANDS)
            or query.excluded_subtypes not in ((), ("lair",))
            or query.excluded_subtypes and query.types_all != ("land",)
            or query.colors_any and (query.types_all != ("creature",) or len(query.colors_any) > 2)
        ):
            raise ValueError("Controlled return predicate is outside the closed battlefield grammar")
        if query.exclude_ref is not None:
            if query.controller == "$controller":
                if query.exclude_ref != "$source.zone_object":
                    raise ValueError("Controlled return must exclude the original source incarnation")
            elif query.exclude_ref.startswith("$"):
                raise ValueError("Controlled return source exclusion must be materialized")

    def _validate_shared_entry_query(self):
        query = self.predicate
        projected = ObjectQuerySpec(
            zones=("battlefield",), controller=query.controller, known_to_actor=True,
            exclude_ref=query.exclude_ref, types_any=query.types_any,
        )
        if ((self.minimum, self.maximum) != (0, 1) or query != projected
                or not set(query.types_any) <= SharedPermanentTypeScope.PERMANENT.types):
            raise ValueError("Shared entry return needs one optional controlled permanent of a matching type")
        if query.controller == "$controller" and (query.types_any or query.exclude_ref is not None):
            raise ValueError("Shared entry types and exclusion must be resolved from the entrant")
        if query.exclude_ref is not None and query.exclude_ref.startswith("$"):
            raise ValueError("Shared entry exclusion must name the current entrant")

    @classmethod
    def shared_entry(cls, *, actor="$controller", types=(), exclude_ref=None):
        return cls(ObjectQuerySpec(zones=("battlefield",), controller=actor, known_to_actor=True,
                                   types_any=types, exclude_ref=exclude_ref),
                   0, 1, schema_version=2)

    def effect(self):
        return {"op": "choose_objects", CONTROLLED_RETURN_FIELD: {
            "schema_version": self.schema_version, "predicate": self.predicate.to_dict(),
            "minimum": self.minimum, "maximum": self.maximum,
        }}

    @classmethod
    def from_effect(cls, effect, *, actor):
        if (not isinstance(effect, Mapping) or set(effect) != _RETURN_FIELDS
                or effect["op"] != "choose_objects"):
            raise ValueError("Controlled return effect has an invalid shape")
        value = thaw_value(effect[CONTROLLED_RETURN_FIELD])
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "predicate", "minimum", "maximum"}
                or type(value["schema_version"]) is not int or value["schema_version"] not in (1, 2)):
            raise ValueError("Controlled return has an invalid versioned instruction")
        scope = cls(ObjectQuerySpec.from_dict(value["predicate"]), value["minimum"], value["maximum"],
                    schema_version=value["schema_version"])
        if scope.predicate.controller != actor:
            raise ValueError("Controlled return must use the resolving controller")
        return scope


def shared_entry_return_condition():
    return {"all": [
        {"not": {"field": "types", "op": "contains_any", "value": ["artifact"]}},
        {"field": "controller", "op": "eq", "value": "$source.controller"},
    ]}


def uses_shared_entry_return(effect):
    return (isinstance(effect, Mapping) and isinstance(entry := effect.get(CONTROLLED_RETURN_FIELD), Mapping)
            and entry.get("schema_version") == 2)


def controlled_return_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or CONTROLLED_RETURN_MECHANIC not in mechanic_ids or CONTROLLED_RETURN_FIELD not in effects[0]):
        return ()
    try:
        scope = ControlledReturn.from_effect(effects[0], actor="$controller")
    except (TypeError, ValueError, KeyError):
        return ()
    return ((CONTROLLED_RETURN_CAPABILITY, SHARED_ENTRY_RETURN_CAPABILITY)
            if scope.schema_version == 2 else (CONTROLLED_RETURN_CAPABILITY,))


def controlled_return_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    required = ()
    for effect in nested_effects(program.effects):
        if CONTROLLED_RETURN_FIELD in effect:
            scope = ControlledReturn.from_effect(effect, actor="$controller")
            if scope.schema_version == 2:
                if ((program.event, program.active_zone) != ("permanent.enter", "battlefield")
                        or program.target_schema is not None or len(program.effects) != 1
                        or thaw_value(program.effects[0]) != scope.effect()
                        or thaw_value(program.event_condition) != shared_entry_return_condition()):
                    raise ValueError("Shared entry return requires the complete controlled nonartifact entry")
                return (CONTROLLED_RETURN_CAPABILITY, SHARED_ENTRY_RETURN_CAPABILITY)
            required = (CONTROLLED_RETURN_CAPABILITY,)
    return required
