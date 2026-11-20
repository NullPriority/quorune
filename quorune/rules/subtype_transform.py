from __future__ import annotations

"""Resolution-time subtype selection for the canonical transform batch."""

from dataclasses import dataclass
from typing import Mapping
from ..creature_subtypes import canonical_creature_subtype
from ..object_query import ObjectQuerySpec

SUBTYPE_TRANSFORM_VERSION = "subtype_transform_version"
SUBTYPE_TRANSFORM_MECHANIC = "fixed-subtype-transform"
SUBTYPE_TRANSFORM_CAPABILITY = "permanent.transform.fixed_subtype_set"


@dataclass(frozen=True, slots=True)
class SubtypeTransformSpec:
    subtype: str

    def __post_init__(self):
        if type(self.subtype) is not str or canonical_creature_subtype(self.subtype) != self.subtype:
            raise ValueError("Transform selection requires one canonical creature type")

    def to_effect(self):
        return {"op": "transform", SUBTYPE_TRANSFORM_VERSION: 1, "subtype": self.subtype}

    @classmethod
    def from_effect(cls, effect):
        if (not isinstance(effect, Mapping)
                or set(effect) - {"_runtime_source"} != {"op", SUBTYPE_TRANSFORM_VERSION, "subtype"}
                or effect.get("op") != "transform"
                or type(effect.get(SUBTYPE_TRANSFORM_VERSION)) is not int
                or effect[SUBTYPE_TRANSFORM_VERSION] != 1):
            raise ValueError("Subtype transform instruction has an unsupported shape")
        return cls(effect["subtype"])

    def query(self):
        return ObjectQuerySpec(zones=("battlefield",), subtypes_all=(self.subtype,))


def subtype_transform_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    if (SUBTYPE_TRANSFORM_MECHANIC not in mechanic_ids
            or target_schema is not None or len(effects) != 1):
        return ()
    try:
        SubtypeTransformSpec.from_effect(effects[0])
    except (KeyError, TypeError, ValueError):
        return ()
    return (SUBTYPE_TRANSFORM_CAPABILITY, "permanent.transform.face_change")


def subtype_transform_runtime_dependencies(program):
    required = set()
    def contains_marker(value):
        if isinstance(value, Mapping):
            return SUBTYPE_TRANSFORM_VERSION in value or any(contains_marker(v) for v in value.values())
        return isinstance(value,(tuple,list)) and any(contains_marker(v) for v in value)
    for effect in program.effects:
        if isinstance(effect,Mapping) and SUBTYPE_TRANSFORM_VERSION in effect:
            SubtypeTransformSpec.from_effect(effect)
            required.update((SUBTYPE_TRANSFORM_CAPABILITY, "permanent.transform.face_change"))
        elif contains_marker(effect):
            raise ValueError("Subtype transform requires a direct resolution instruction")
    return tuple(sorted(required))


def apply_subtype_transform(host, effect, *, actor, reason):
    from ..continuous_effect_state import matching_battlefield_objects
    from ..permanent_transform import commit_transform_batch
    from ..errors import GameRuleError
    try:
        spec = SubtypeTransformSpec.from_effect(effect)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    cards = tuple(matching_battlefield_objects(host, spec.query()))
    return commit_transform_batch(host, cards, reason=reason,
        day_night_instruction=False,
        expected_transform_counts={card.object_id: card.transform_count for card in cards})
