"""Validate the typed source-scoped off-battlefield characteristic fragment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..off_battlefield_creatures import (
    OFF_BATTLEFIELD_SELF_CREATURE_CAPABILITY,
    OFF_BATTLEFIELD_SELF_CREATURE_HANDLER,
    OffBattlefieldSelfCreatureSpec,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class OffBattlefieldSelfCreatureHandler:
    handler_id: str = OFF_BATTLEFIELD_SELF_CREATURE_HANDLER
    schema_version: int = 1
    family: str = OFF_BATTLEFIELD_SELF_CREATURE_CAPABILITY
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("113.6b", "205.1b", "604.3a", "613.1d", "613.4b", "903.3")
    capability_dependencies: tuple[str, ...] = (OFF_BATTLEFIELD_SELF_CREATURE_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> OffBattlefieldSelfCreatureSpec:
        exact_fields(descriptor,
                     {"handler_id", "schema_version", "event", "requires_complete_card_program", "characteristics"},
                     field="off-battlefield self creature characteristics")
        if (descriptor["handler_id"] != self.handler_id
                or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version
                or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Off-battlefield self characteristics require their exact complete-source handler")
        try:
            return OffBattlefieldSelfCreatureSpec.from_dict(descriptor["characteristics"])
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor: Mapping[str, Any], context: object) -> tuple[OffBattlefieldSelfCreatureSpec, ...]:
        return (self.validate(descriptor),)
