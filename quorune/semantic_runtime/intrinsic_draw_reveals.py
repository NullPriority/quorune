from __future__ import annotations

"""Intrinsic card declarations at the canonical as-drawn reveal boundary."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..drawing.miracle_model import (
    FixedMiracleCost, MiracleModelError, MIRACLE_CAPABILITY_ID,
    MIRACLE_REVEAL_EVENT, MIRACLE_REVEAL_HANDLER_ID,
    MIRACLE_TRIGGER_EVENT, MIRACLE_TRIGGER_HANDLER_ID,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


def miracle_reveal_descriptor(cost: FixedMiracleCost) -> dict[str, Any]:
    if not isinstance(cost, FixedMiracleCost):
        raise SemanticNodeError("Miracle reveal requires a typed fixed cost")
    return {
        "handler_id": MIRACLE_REVEAL_HANDLER_ID,
        "schema_version": 1,
        "event": MIRACLE_REVEAL_EVENT,
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "cost": cost.to_dict(),
    }


def miracle_trigger_descriptor(cost: FixedMiracleCost) -> dict[str, Any]:
    return {
        **miracle_reveal_descriptor(cost),
        "handler_id": MIRACLE_TRIGGER_HANDLER_ID,
        "event": MIRACLE_TRIGGER_EVENT,
    }


@dataclass(frozen=True, slots=True)
class MiracleIntrinsicRevealHandler:
    handler_id: str = MIRACLE_REVEAL_HANDLER_ID
    schema_version: int = 1
    family: str = "action.draw.miracle_reveal"
    event: str = MIRACLE_REVEAL_EVENT
    rule_references: tuple[str, ...] = ("121.9", "702.94a", "702.94b")
    capability_dependencies: tuple[str, ...] = (MIRACLE_CAPABILITY_ID,)

    def validate(self, descriptor: Mapping[str, Any]) -> FixedMiracleCost:
        if not isinstance(descriptor, Mapping):
            raise SemanticNodeError("Miracle reveal descriptor must be an object")
        exact_fields(descriptor, {"handler_id", "schema_version", "event",
                                 REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, "cost"},
                     field="Miracle reveal descriptor")
        if (descriptor["handler_id"] != self.handler_id
                or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version
                or descriptor["event"] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError("Miracle reveal identity, version or admission changed")
        try:
            return FixedMiracleCost.from_dict(descriptor["cost"])
        except MiracleModelError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor: Mapping[str, Any], context: object) -> tuple[FixedMiracleCost, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class MiracleCastTriggerHandler(MiracleIntrinsicRevealHandler):
    handler_id: str = MIRACLE_TRIGGER_HANDLER_ID
    family: str = "casting.miracle.trigger"
    event: str = MIRACLE_TRIGGER_EVENT
    rule_references: tuple[str, ...] = ("400.7", "603.11", "702.94a", "702.94b")
