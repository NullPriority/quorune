"""Closed quantities over the committed results of one destruction instruction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import (
    CommittedQuantityFollowup,
    fixed_followup_capabilities,
    fixed_quantity_effect,
    quantity_followup_from_count,
)


DESTRUCTION_RESULT_CAPABILITY = "permanent.destroy.result_quantity"
DESTRUCTION_RESULT_MECHANIC = "committed-destruction-quantity"

@dataclass(frozen=True, slots=True)
class DestructionFollowup:
    scope: AffectedPermanentSetSpec
    effect: FrozenMap

    def __post_init__(self) -> None:
        if not isinstance(self.scope, AffectedPermanentSetSpec):
            raise ValueError("Destruction result requires a typed battlefield scope")
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
        object.__setattr__(self, "effect", fixed_quantity_effect(self.effect))

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        return (DESTRUCTION_RESULT_CAPABILITY, *fixed_followup_capabilities(self.effect))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "scope": self.scope.to_dict(), "effect": thaw_value(self.effect)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DestructionFollowup:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "scope", "effect"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
        ):
            raise ValueError("Destruction follow-up descriptor is malformed")
        return cls(AffectedPermanentSetSpec.from_dict(value["scope"]), value["effect"])

    def from_committed_count(self, count: int) -> CommittedQuantityFollowup:
        return quantity_followup_from_count(self.effect, count)
