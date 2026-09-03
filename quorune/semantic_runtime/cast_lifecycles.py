from __future__ import annotations

"""Runtime descriptor ownership for fixed public casting lifecycles."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..cast_lifecycles import (
    FixedCastLifecycleError,
    FixedCastLifecycleSpec,
    FIXED_CAST_LIFECYCLE_CAPABILITY_ID,
    FIXED_CAST_LIFECYCLE_HANDLER_ID,
    FIXED_CAST_LIFECYCLE_RUNTIME_EVENT,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedCastLifecycleHandler:
    handler_id: str = FIXED_CAST_LIFECYCLE_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.lifecycle.fixed_public"
    event: str = FIXED_CAST_LIFECYCLE_RUNTIME_EVENT
    rule_references: tuple[str, ...] = (
        "601.2b",
        "601.2f",
        "601.2h",
        "702.27",
        "702.81",
        "702.109",
        "702.185",
    )
    capability_dependencies: tuple[str, ...] = (
        FIXED_CAST_LIFECYCLE_CAPABILITY_ID,
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> FixedCastLifecycleSpec:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
                "lifecycle",
            },
            field="fixed cast-lifecycle handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError(
                "Fixed cast-lifecycle identity, version, or event changed"
            )
        if descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True:
            raise SemanticNodeError(
                "Fixed cast lifecycles require complete-card admission"
            )
        try:
            return FixedCastLifecycleSpec.from_dict(
                descriptor["lifecycle"]
            )
        except (FixedCastLifecycleError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedCastLifecycleSpec, ...]:
        del context
        return (self.validate(descriptor),)


class FixedCastLifecycleRegistry(
    RuntimeComponentRegistry[object, FixedCastLifecycleSpec]
):
    pass


@lru_cache(maxsize=1)
def default_fixed_cast_lifecycle_registry() -> FixedCastLifecycleRegistry:
    registry = FixedCastLifecycleRegistry((FixedCastLifecycleHandler(),))
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


__all__ = [
    "default_fixed_cast_lifecycle_registry",
    "FixedCastLifecycleHandler",
    "FixedCastLifecycleRegistry",
]
