from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..cast_timing import (
    CAST_PERMISSION_EVENT,
    CastTimingPermission,
    CastTimingPermissionError,
    CLASSIC_FLASH_CAPABILITY,CLASSIC_FLASH_HANDLER,
)
from ..rules.capabilities import load_default_capability_registry
from ..continuous_conditions import FixedPublicStateConditionSpec
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


FLASH_CAST_PERMISSION_HANDLER_ID = "ability.static.flash.v1"


@dataclass(frozen=True, slots=True)
class FlashCastPermissionHandler:
    handler_id: str = FLASH_CAST_PERMISSION_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.flash"
    event: str = CAST_PERMISSION_EVENT
    rule_references: tuple[str, ...] = (
        "117.1a",
        "304.5",
        "307.5",
        "702.8",
        "702.8a",
        "702.8b",
    )
    capability_dependencies: tuple[str, ...] = (
        "timing.cast.printed_flash",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CastTimingPermission:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "permission"},
            field="Flash cast-permission handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Flash cast-permission handler ID mismatch")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported Flash cast-permission handler schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"Flash cast-permission handler must use {self.event}"
            )
        permission = descriptor["permission"]
        if not isinstance(permission, Mapping):
            raise SemanticNodeError(
                "Flash cast-permission value must be an object"
            )
        try:
            return CastTimingPermission.from_dict(permission)
        except CastTimingPermissionError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[CastTimingPermission, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True,slots=True)
class ClassicFlashPermissionHandler(FlashCastPermissionHandler):
    handler_id: str=CLASSIC_FLASH_HANDLER
    family: str='permission.cast.classic_flash_cleanup'
    rule_references: tuple[str,...]=('117.1a','601.2','603.7g','514.3a','609.4')
    capability_dependencies: tuple[str,...]=(CLASSIC_FLASH_CAPABILITY,)


class CastPermissionRegistry(
    RuntimeComponentRegistry[object, CastTimingPermission | FixedPublicStateConditionSpec]
):
    pass


@lru_cache(maxsize=1)
def default_cast_permission_registry() -> CastPermissionRegistry:
    from .conditional_flash import ConditionalFlashPermissionHandler
    from .self_cast_restrictions import SelfCastRestrictionHandler
    registry = CastPermissionRegistry((FlashCastPermissionHandler(),ConditionalFlashPermissionHandler(),
        SelfCastRestrictionHandler(),ClassicFlashPermissionHandler()))
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


__all__ = [
    "FLASH_CAST_PERMISSION_HANDLER_ID",
    "CastPermissionRegistry",
    "FlashCastPermissionHandler",
    "default_cast_permission_registry",
]
