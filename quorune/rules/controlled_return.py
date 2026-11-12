from __future__ import annotations

"""Closed descriptors for one bounded controller-selected permanent return."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value

CONTROLLED_RETURN_FIELD = "controlled_return"
CONTROLLED_RETURN_CAPABILITY = "choice.controller.bounded_return_owner_hand"
CONTROLLED_RETURN_MECHANIC = "bounded-controlled-return"
_RETURN_FIELDS = {"op", CONTROLLED_RETURN_FIELD}
_BASIC_LANDS = {"plains", "island", "swamp", "mountain", "forest"}


@dataclass(frozen=True, slots=True)
class ControlledReturn:
    predicate: ObjectQuerySpec
    minimum: int
    maximum: int

    def __post_init__(self):
        if (type(self.minimum) is not int or type(self.maximum) is not int
                or (self.minimum, self.maximum) not in {(0, 1), (0, 2), (0, 3), (1, 1)}):
            raise ValueError("Controlled return needs one fixed or bounded optional count")
        query = self.predicate
        if not isinstance(query, ObjectQuerySpec) or not query.controller:
            raise ValueError("Controlled return needs an authenticated controller predicate")
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

    def effect(self):
        return {"op": "choose_objects", CONTROLLED_RETURN_FIELD: {
            "schema_version": 1, "predicate": self.predicate.to_dict(),
            "minimum": self.minimum, "maximum": self.maximum,
        }}

    @classmethod
    def from_effect(cls, effect, *, actor):
        if (not isinstance(effect, Mapping) or set(effect) != _RETURN_FIELDS
                or effect["op"] != "choose_objects"):
            raise ValueError("Controlled return effect has an invalid shape")
        value = thaw_value(effect[CONTROLLED_RETURN_FIELD])
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "predicate", "minimum", "maximum"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Controlled return has an invalid versioned instruction")
        scope = cls(ObjectQuerySpec.from_dict(value["predicate"]), value["minimum"], value["maximum"])
        if scope.predicate.controller != actor:
            raise ValueError("Controlled return must use the resolving controller")
        return scope


def controlled_return_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or CONTROLLED_RETURN_MECHANIC not in mechanic_ids or CONTROLLED_RETURN_FIELD not in effects[0]):
        return ()
    try:
        ControlledReturn.from_effect(effects[0], actor="$controller")
    except (TypeError, ValueError, KeyError):
        return ()
    return (CONTROLLED_RETURN_CAPABILITY,)


def controlled_return_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    required = ()
    for effect in nested_effects(program.effects):
        if CONTROLLED_RETURN_FIELD in effect:
            ControlledReturn.from_effect(effect, actor="$controller")
            required = (CONTROLLED_RETURN_CAPABILITY,)
    return required
