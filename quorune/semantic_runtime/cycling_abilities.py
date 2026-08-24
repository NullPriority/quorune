from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..cycling_abilities import (
    CYCLING_HANDLER_ID,
    TYPECYCLING_HANDLER_ID,
    CyclingAbilityError,
    OrdinaryCyclingAbilitySpec,
    TypecyclingAbilitySpec,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class OrdinaryCyclingAbilityHandler:
    handler_id: str = CYCLING_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.cycling"
    event: str = "activate"
    rule_references: tuple[str, ...] = (
        "602.1",
        "602.2",
        "702.29",
        "702.29a",
        "702.29b",
    )
    capability_dependencies: tuple[str, ...] = (
        "activation.cycling.hand",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> OrdinaryCyclingAbilitySpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "ability"},
            field="ordinary Cycling handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Cycling handler ID mismatch")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported ordinary Cycling handler schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Ordinary Cycling handler must use the activate event"
            )
        ability = descriptor["ability"]
        if not isinstance(ability, Mapping):
            raise SemanticNodeError("Cycling ability must be an object")
        try:
            return OrdinaryCyclingAbilitySpec.from_dict(ability)
        except CyclingAbilityError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[OrdinaryCyclingAbilitySpec, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class TypecyclingAbilityHandler:
    handler_id: str = TYPECYCLING_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.cycling"
    event: str = "activate"
    rule_references: tuple[str, ...] = (
        "602.1",
        "602.2",
        "701.23",
        "702.29",
        "702.29c",
        "702.29d",
    )
    capability_dependencies: tuple[str, ...] = (
        "activation.typecycling.hand",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> TypecyclingAbilitySpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "ability"},
            field="Typecycling handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Typecycling handler ID mismatch")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported Typecycling handler schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Typecycling handler must use the activate event"
            )
        ability = descriptor["ability"]
        if not isinstance(ability, Mapping):
            raise SemanticNodeError("Typecycling ability must be an object")
        try:
            return TypecyclingAbilitySpec.from_dict(ability)
        except CyclingAbilityError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[TypecyclingAbilitySpec, ...]:
        del context
        return (self.validate(descriptor),)


class CyclingAbilityRegistry(
    RuntimeComponentRegistry[
        object, OrdinaryCyclingAbilitySpec | TypecyclingAbilitySpec
    ]
):
    pass


@lru_cache(maxsize=1)
def default_cycling_ability_registry() -> CyclingAbilityRegistry:
    registry = CyclingAbilityRegistry(
        (OrdinaryCyclingAbilityHandler(), TypecyclingAbilityHandler())
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


OrdinaryCyclingAbilityRegistry = CyclingAbilityRegistry


def default_ordinary_cycling_ability_registry() -> CyclingAbilityRegistry:
    """Compatibility alias for the registry now shared with Typecycling."""

    return default_cycling_ability_registry()


def ordinary_cycling_specs_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[OrdinaryCyclingAbilitySpec, ...]:
    registry = default_cycling_ability_registry()
    result: list[OrdinaryCyclingAbilitySpec] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        result.extend(
            spec
            for spec in registry.lower(descriptor, None)
            if isinstance(spec, OrdinaryCyclingAbilitySpec)
        )
    return tuple(result)


def cycling_specs_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[OrdinaryCyclingAbilitySpec | TypecyclingAbilitySpec, ...]:
    registry = default_cycling_ability_registry()
    result: list[OrdinaryCyclingAbilitySpec | TypecyclingAbilitySpec] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        result.extend(registry.lower(descriptor, None))
    return tuple(result)


__all__ = [
    "CyclingAbilityRegistry",
    "OrdinaryCyclingAbilityHandler",
    "OrdinaryCyclingAbilityRegistry",
    "TypecyclingAbilityHandler",
    "cycling_specs_from_descriptors",
    "default_cycling_ability_registry",
    "default_ordinary_cycling_ability_registry",
    "ordinary_cycling_specs_from_descriptors",
]
