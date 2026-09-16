from __future__ import annotations

"""Runtime validation for typed Class level activation descriptors."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..class_levels import (
    CLASS_LEVEL_ACTIVATION_HANDLER_ID,
    CLASS_LIFECYCLE_CAPABILITY_ID,
    ClassLevelError,
    FixedClassLevelAbilitySpec,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class ClassLevelActivationHandler:
    handler_id: str = CLASS_LEVEL_ACTIVATION_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.class-level"
    event: str = "activate"
    rule_references: tuple[str, ...] = (
        "107.16",
        "107.16a",
        "716.2",
        "716.2a",
        "716.2b",
        "716.2d",
    )
    capability_dependencies: tuple[str, ...] = (
        CLASS_LIFECYCLE_CAPABILITY_ID,
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedClassLevelAbilitySpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "ability"},
            field="Class level activation handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Class level activation handler ID mismatch")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported Class level activation handler version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Class level activation handler must use activate"
            )
        ability = descriptor["ability"]
        if not isinstance(ability, Mapping):
            raise SemanticNodeError(
                "Class level activation ability must be an object"
            )
        try:
            return FixedClassLevelAbilitySpec.from_dict(ability)
        except ClassLevelError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedClassLevelAbilitySpec, ...]:
        del context
        return (self.validate(descriptor),)


class ClassLevelActivationRegistry(
    RuntimeComponentRegistry[object, FixedClassLevelAbilitySpec]
):
    pass


@lru_cache(maxsize=1)
def default_class_level_activation_registry() -> ClassLevelActivationRegistry:
    registry = ClassLevelActivationRegistry((ClassLevelActivationHandler(),))
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def class_level_specs_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[FixedClassLevelAbilitySpec, ...]:
    registry = default_class_level_activation_registry()
    result: list[FixedClassLevelAbilitySpec] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        result.extend(registry.lower(descriptor, None))
    return tuple(result)


__all__ = [
    "ClassLevelActivationHandler",
    "ClassLevelActivationRegistry",
    "class_level_specs_from_descriptors",
    "default_class_level_activation_registry",
]
