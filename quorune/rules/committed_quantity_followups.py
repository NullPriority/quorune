"""Closed scalar follow-ups shared by committed action results."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap, thaw_value


_QUANTITY_FIELDS = {
    "draw": "count",
    "life": "delta",
    "lose_life": "amount",
    "create_token": "quantity",
    "place_counters": "amount",
}


def fixed_followup_capabilities(effect: Mapping[str, Any]) -> tuple[str, ...]:
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
            from .token_creation_capability_shapes import (
                fixed_token_creation_node_capabilities,
                token_creation_component_mechanics,
            )

            from .capabilities import capability_dependencies_for_node

            effects = (thaw_value(effect),)
            mechanics = token_creation_component_mechanics(effects, set())
            if fixed_token_creation_node_capabilities(
                effects=effects, target_schema=None, mechanic_ids=mechanics,
            ):
                return capability_dependencies_for_node(
                    effects=effects, target_schema=None, mechanic_ids=mechanics,
                )
    return ()


@dataclass(frozen=True, slots=True)
class CommittedQuantityFollowup:
    """An instruction emitted only after its preceding action commits."""

    effects: tuple[FrozenMap, ...]


def fixed_quantity_effect(effect: Mapping[str, Any]) -> FrozenMap:
    if not isinstance(effect, Mapping):
        raise ValueError("Committed result requires a typed follow-up instruction")
    effect = FrozenMap(effect)
    if not fixed_followup_capabilities(effect):
        raise ValueError("Committed follow-up lacks a closed quantity slot")
    return effect


def quantity_followup_from_count(effect: FrozenMap, count: int) -> CommittedQuantityFollowup:
    if type(count) is not int or count < 0:
        raise ValueError("Committed result count is malformed")
    if count == 0:
        return CommittedQuantityFollowup(())
    resolved = thaw_value(effect)
    field = _QUANTITY_FIELDS[resolved["op"]]
    resolved[field] *= count
    return CommittedQuantityFollowup((FrozenMap(resolved),))
