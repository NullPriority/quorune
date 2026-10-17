from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..landwalk_permission_model import (
    LANDWALK_PERMISSION_CAPABILITY, LANDWALK_PERMISSION_HANDLER, LandwalkBlockPermission,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class LandwalkPermissionFragmentHandler:
    handler_id: str = LANDWALK_PERMISSION_HANDLER
    schema_version: int = 1
    family: str = "ability.static.landwalk_block_permission"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("609.4", "702.14", "509.1b")
    capability_dependencies: tuple[str, ...] = (LANDWALK_PERMISSION_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> LandwalkBlockPermission:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "fragment"}, field="Landwalk block permission")
        if (descriptor["handler_id"] != self.handler_id
                or type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != 1
                or descriptor["event"] != self.event):
            raise SemanticNodeError("Landwalk block-permission identity or schema mismatch")
        fragment=descriptor["fragment"]
        exact_fields(fragment, {"kind", "value"}, field="Landwalk block-permission fragment")
        if fragment["kind"] != "landwalk_block_permission":
            raise SemanticNodeError("Landwalk block-permission fragment kind mismatch")
        try:
            return LandwalkBlockPermission.from_dict(fragment["value"])
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor: Mapping[str, Any], context: object) -> tuple[LandwalkBlockPermission, ...]:
        del context
        return (self.validate(descriptor),)
