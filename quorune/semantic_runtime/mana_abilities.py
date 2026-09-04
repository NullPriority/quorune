from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..fixed_mana_abilities import (
    FIXED_MANA_HANDLER_ID,
    FixedActivatedManaAbilitySpec,
    FixedManaAbilityError,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedActivatedManaAbilityHandler:
    handler_id: str = FIXED_MANA_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.mana.fixed-output"
    event: str = "activate"
    rule_references: tuple[str, ...] = (
        "605.1a",
        "605.2",
        "605.3a",
        "605.3b",
    )
    capability_dependencies: tuple[str, ...] = (
        "mana.activated.fixed_output",
        "mana.activated.restricted_fixed_output",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedActivatedManaAbilitySpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "ability"},
            field="fixed activated mana handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Fixed activated mana handler ID mismatch")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported fixed activated mana handler schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Fixed activated mana handler must use the activate event"
            )
        ability = descriptor["ability"]
        if not isinstance(ability, Mapping):
            raise SemanticNodeError("Fixed activated mana ability must be an object")
        try:
            return FixedActivatedManaAbilitySpec.from_dict(ability)
        except FixedManaAbilityError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedActivatedManaAbilitySpec, ...]:
        del context
        return (self.validate(descriptor),)


class FixedManaAbilityRegistry(
    RuntimeComponentRegistry[object, FixedActivatedManaAbilitySpec]
):
    pass


@lru_cache(maxsize=1)
def default_fixed_mana_ability_registry() -> FixedManaAbilityRegistry:
    registry = FixedManaAbilityRegistry((FixedActivatedManaAbilityHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


def fixed_mana_specs_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[FixedActivatedManaAbilitySpec, ...]:
    registry = default_fixed_mana_ability_registry()
    result: list[FixedActivatedManaAbilitySpec] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        result.extend(registry.lower(descriptor, None))
    return tuple(result)


__all__ = [
    "FixedActivatedManaAbilityHandler",
    "FixedManaAbilityRegistry",
    "default_fixed_mana_ability_registry",
    "fixed_mana_specs_from_descriptors",
]
