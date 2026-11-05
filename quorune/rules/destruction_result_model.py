"""Closed quantities over the committed results of one destruction instruction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value


DESTRUCTION_RESULT_CAPABILITY = "permanent.destroy.result_quantity"
DESTRUCTION_RESULT_MECHANIC = "committed-destruction-quantity"

_QUANTITY_FIELDS = {
    "draw": "count",
    "life": "delta",
    "lose_life": "amount",
    "create_token": "quantity",
    "place_counters": "amount",
}


def _followup_capabilities(effect: Mapping[str, Any]) -> tuple[str, ...]:
    operation = effect.get("op")
    if operation == "draw" and dict(effect) == {
        "op": "draw", "player": "$controller", "count": 1, "private": True,
    } and type(effect["count"]) is int:
        return ("zone.draw.library_to_hand",)
    if operation in ("life", "lose_life") and set(effect) == {
        "op", "player", _QUANTITY_FIELDS[operation],
    }:
        quantity = effect[_QUANTITY_FIELDS[operation]]
        if effect["player"] == "$controller" and type(quantity) is int and quantity in (1, 2):
            return ("life.change.effect",)
    if operation == "place_counters" and dict(effect) == {
        "op": "place_counters", "card": "$source.zone_object",
        "counter": "+1/+1", "amount": 1, "source": "$source",
    } and type(effect["amount"]) is int:
        return ("counter.producer.fixed_effect",)
    if operation == "create_token":
        fields = {"op", "controller", "quantity", "name", "characteristics"}
        if (
            set(effect) in (fields, fields | {"tapped"})
            and effect.get("controller") == "$controller"
            and type(effect.get("quantity")) is int and effect["quantity"] == 1
            and ("tapped" not in effect or effect["tapped"] is True)
        ):
            from .token_creation_capability_shapes import fixed_token_creation_node_capabilities

            return fixed_token_creation_node_capabilities(
                effects=(thaw_value(effect),), target_schema=None, mechanic_ids={"cr-111-tokens"},
            )
    return ()


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
        if not isinstance(self.effect, Mapping):
            raise ValueError("Destruction result requires a typed follow-up instruction")
        effect = FrozenMap(self.effect)
        if not _followup_capabilities(effect):
            raise ValueError("Destruction follow-up lacks a closed quantity slot")
        object.__setattr__(self, "effect", effect)

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        return (DESTRUCTION_RESULT_CAPABILITY, *_followup_capabilities(self.effect))

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

    def from_committed_count(self, count: int) -> CommittedDestructionFollowup:
        if type(count) is not int or count < 0:
            raise ValueError("Committed destruction count is malformed")
        if count == 0:
            return CommittedDestructionFollowup(())
        effect = thaw_value(self.effect)
        field = _QUANTITY_FIELDS[effect["op"]]
        effect[field] *= count
        return CommittedDestructionFollowup((FrozenMap(effect),))


@dataclass(frozen=True, slots=True)
class CommittedDestructionFollowup:
    """An instruction emitted only after the destruction transaction commits."""

    effects: tuple[FrozenMap, ...]
