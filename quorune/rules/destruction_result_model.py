"""Closed destruction result descriptors and committed quantities."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import (
    COMMITTED_TOKEN_CREATOR_COUNTS_FIELD,
    CommittedQuantityFollowup,
    fixed_followup_capabilities,
    fixed_quantity_effect,
    quantity_followup_from_count,
    token_followup_from_committed_controllers,
)


DESTRUCTION_RESULT_CAPABILITY = "permanent.destroy.result_quantity"
DESTRUCTION_RESULT_MECHANIC = "committed-destruction-quantity"
DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY = "permanent.destroy.controller_token_result"
DESTRUCTION_TOKEN_COUNTS_FIELD = COMMITTED_TOKEN_CREATOR_COUNTS_FIELD

_CONTROLLER_TOKEN_DESTRUCTION_SCOPES = (
    AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))),
    AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",), types_any=("artifact", "enchantment"))),
)


@dataclass(frozen=True, slots=True)
class DestructionFollowup:
    scope: AffectedPermanentSetSpec
    effect: FrozenMap
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.scope, AffectedPermanentSetSpec):
            raise ValueError("Destruction result requires a typed battlefield scope")
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise ValueError("Destruction follow-up version is unsupported")
        effect = fixed_quantity_effect(self.effect)
        if self.uses_destroyed_controllers:
            definition = effect.get("characteristics", {})
            type_line = definition.get("type_line", "")
            if (self.scope not in _CONTROLLER_TOKEN_DESTRUCTION_SCOPES
                    or effect.get("op") != "create_token" or "tapped" in effect
                    or not type_line.startswith("Token Creature — ")):
                raise ValueError("Controller tokens require a fixed creature token and an exact destruction set")
        else:
            self._validate_quantity_scope()
        object.__setattr__(self, "effect", effect)

    def _validate_quantity_scope(self) -> None:
        query = self.scope.query
        if (
            self.scope.controller_relation not in (PermanentControllerRelation.ANY, PermanentControllerRelation.ACTOR)
            or self.scope.exclude_source
            or query.types_all not in ((), ("creature",), ("enchantment",))
            or query.token not in (None, False)
            or (query.token is False or self.scope.controller_relation is PermanentControllerRelation.ACTOR)
            and query.types_all != ("creature",)
            or query != ObjectQuerySpec(zones=("battlefield",), types_all=query.types_all, token=query.token)
        ):
            raise ValueError("Destruction result scope is unsupported")

    @property
    def uses_destroyed_controllers(self) -> bool:
        return self.schema_version == 2

    def validate_destruction_set(self, spec: AffectedPermanentSetSpec) -> None:
        if self.uses_destroyed_controllers and spec != self.scope:
            raise ValueError("Controller tokens require the same complete set as their destruction")

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        result = DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY if self.uses_destroyed_controllers else DESTRUCTION_RESULT_CAPABILITY
        return (result, *fixed_followup_capabilities(self.effect))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "scope": self.scope.to_dict(), "effect": thaw_value(self.effect)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DestructionFollowup:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "scope", "effect"}:
            raise ValueError("Destruction follow-up descriptor is malformed")
        return cls(AffectedPermanentSetSpec.from_dict(value["scope"]), value["effect"], value["schema_version"])

    @classmethod
    def from_destruction_effect(cls, effect: Mapping[str, Any]) -> DestructionFollowup:
        followup = cls.from_dict(effect["followup"])
        if followup.uses_destroyed_controllers:
            followup.validate_destruction_set(AffectedPermanentSetSpec.from_dict(effect["set"]))
        return followup

    def from_committed_count(self, count: int) -> CommittedQuantityFollowup:
        return quantity_followup_from_count(self.effect, count)

    def from_committed_controllers(self, controllers: Iterable[str]) -> CommittedQuantityFollowup:
        return token_followup_from_committed_controllers(self.effect, controllers)


NEXT_END_STEP_DESTRUCTION_FIELD = "next_end_step_destruction_version"
NEXT_END_STEP_DESTRUCTION_CAPABILITY = "trigger.delayed.fixed_permanent_set_destruction"
NEXT_END_STEP_DESTRUCTION_DEPENDENCIES = (
    NEXT_END_STEP_DESTRUCTION_CAPABILITY,
    "permanent.destroy.fixed_set",
    "trigger.placement.apnap",
)
_NEXT_END_STEP_DESTRUCTION_LABEL = "Destroy all permanents at the next end step"
_NEXT_END_STEP_DESTRUCTION_SET = AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",)))


def next_end_step_destruction_effect() -> dict[str, Any]:
    from ..target_source import TARGETING_SOURCE_CONTEXT

    return {
        "op": "delayed_trigger",
        NEXT_END_STEP_DESTRUCTION_FIELD: 1,
        "controller": "$controller",
        "label": _NEXT_END_STEP_DESTRUCTION_LABEL,
        "event": "step.begin",
        "condition": {"phase": "ending", "step": "end_step"},
        "source_binding": f"$context.{TARGETING_SOURCE_CONTEXT}",
        "once": True,
        "stack": {
            "label": _NEXT_END_STEP_DESTRUCTION_LABEL,
            "context": {"dynamic_effects": [{
                "op": "destroy_all",
                "source": None,
                "set": _NEXT_END_STEP_DESTRUCTION_SET.to_dict(),
            }]},
        },
    }


def validate_next_end_step_destruction(value: Mapping[str, Any], *, actor: str | None = None) -> None:
    """Validate only one ordinary untargeted destruction at the next real end step."""
    if not isinstance(value, Mapping):
        raise ValueError("Delayed destruction requires a typed instruction")
    actual = thaw_value(value)
    version = actual.get(NEXT_END_STEP_DESTRUCTION_FIELD)
    expected = next_end_step_destruction_effect()
    if type(version) is not int or version != 1 or actual.get("once") is not True:
        raise ValueError("Delayed destruction requires its exact one-shot instruction")
    if actor is not None:
        from ..target_source import TargetingSourceBinding

        actual.pop("_runtime_source", None)
        actual.pop("reason", None)
        binding = TargetingSourceBinding.from_dict(actual.get("source_binding"))
        if binding.zone != "battlefield":
            raise ValueError("Delayed destruction requires its original battlefield source")
        expected.update(controller=actor, source_binding=binding.to_dict())
    if actual != expected:
        raise ValueError("Delayed destruction has a closed end-step and battlefield result")
    leaf = actual["stack"]["context"]["dynamic_effects"][0]
    if AffectedPermanentSetSpec.from_dict(leaf["set"]) != _NEXT_END_STEP_DESTRUCTION_SET:
        raise ValueError("Delayed destruction requires the ordinary all-permanents set")


CREATURE_TYPE_DESTRUCTION_FIELD = "creature_type_destruction"
CREATURE_TYPE_DESTRUCTION_CAPABILITY = "permanent.destroy.chosen_creature_type_set"
CREATURE_TYPE_DESTRUCTION_DEPENDENCIES = (
    CREATURE_TYPE_DESTRUCTION_CAPABILITY,
    "permanent.destroy.fixed_set",
)


class ChosenCreatureTypeDestruction(str, Enum):
    MATCHING = "matching"
    NONMATCHING = "nonmatching"

    def choice_effect(self) -> dict[str, Any]:
        return {
            "op": "choose_creature_type",
            CREATURE_TYPE_DESTRUCTION_FIELD: {"schema_version": 1, "scope": self.value},
        }

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any]) -> ChosenCreatureTypeDestruction:
        if (
            not isinstance(effect, Mapping)
            or set(effect) != {"op", CREATURE_TYPE_DESTRUCTION_FIELD}
            or effect["op"] != "choose_creature_type"
        ):
            raise ValueError("Creature-type destruction requires its exact choice instruction")
        spec = effect[CREATURE_TYPE_DESTRUCTION_FIELD]
        if (
            not isinstance(spec, Mapping)
            or set(spec) != {"schema_version", "scope"}
            or type(spec["schema_version"]) is not int
            or spec["schema_version"] != 1
            or type(spec["scope"]) is not str
        ):
            raise ValueError("Creature-type destruction has a closed versioned scope")
        return cls(spec["scope"])

    def destruction_effect(self, creature_type: str) -> dict[str, Any]:
        """Make an ordinary simultaneous destruction instruction for the chosen type."""
        from ..creature_subtypes import CREATURE_SUBTYPES

        if type(creature_type) is not str or creature_type not in CREATURE_SUBTYPES:
            raise ValueError("Creature-type destruction requires one canonical creature type")
        subtype_filter = (
            {"subtypes_all": (creature_type,)} if self is self.MATCHING
            else {"excluded_subtypes": (creature_type,)}
        )
        scope = AffectedPermanentSetSpec(query=ObjectQuerySpec(
            zones=("battlefield",), types_all=("creature",), **subtype_filter,
        ))
        return {"op": "destroy_all", "source": "$source", "set": scope.to_dict()}
