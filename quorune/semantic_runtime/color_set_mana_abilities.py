from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..color_set_mana_abilities import (
    COLOR_SET_MANA_HANDLER_ID,
    COMMANDER_ENTRY_COUNTER_MANA_HANDLER_ID,
    COMMANDER_IDENTITY_MANA_HANDLER_ID,
    COMMANDER_IDENTITY_MANA_CAPABILITY,
    COMMANDER_TYPE_SCRY_MANA_HANDLER_ID,
    ColorSetActivatedManaAbilitySpec,
    ColorSetManaAbilityError,
)
from ..rules.linked_hand_mana_model import MANA_HANDLER, REQUIREMENTS
from ..mana_spend_rules import COMMANDER_ENTRY_COUNTER_MANA_CAPABILITY, COMMANDER_TYPE_SCRY_CAPABILITY
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class ColorSetActivatedManaAbilityHandler:
    handler_id: str = COLOR_SET_MANA_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.mana.color-set"
    event: str = "activate"
    rule_references: tuple[str, ...] = (
        "106.1",
        "106.4",
        "605.1a",
        "605.2",
        "605.3a",
        "605.3b",
    )
    capability_dependencies: tuple[str, ...] = (
        "mana.activated.color_set",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> ColorSetActivatedManaAbilitySpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "ability"},
            field="color-set activated mana handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Color-set activated mana handler ID mismatch")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported color-set activated mana handler schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Color-set activated mana handler must use the activate event"
            )
        ability = descriptor["ability"]
        if not isinstance(ability, Mapping):
            raise SemanticNodeError(
                "Color-set activated mana ability must be an object"
            )
        try:
            spec = ColorSetActivatedManaAbilitySpec.from_dict(ability)
            if (spec.uses_commander_identity != (self.handler_id not in (COLOR_SET_MANA_HANDLER_ID, MANA_HANDLER))
                    or (spec.linked_exile_pair is not None) != (self.handler_id == MANA_HANDLER)
                    or spec.commander_type_scry != (self.handler_id == COMMANDER_TYPE_SCRY_MANA_HANDLER_ID)
                    or spec.commander_entry_counters != (self.handler_id == COMMANDER_ENTRY_COUNTER_MANA_HANDLER_ID)):
                raise ColorSetManaAbilityError("Mana output source does not match its registered authority")
            return spec
        except (ColorSetManaAbilityError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[ColorSetActivatedManaAbilitySpec, ...]:
        del context
        return (self.validate(descriptor),)


class ColorSetManaAbilityRegistry(
    RuntimeComponentRegistry[object, ColorSetActivatedManaAbilitySpec]
):
    pass


@lru_cache(maxsize=1)
def default_color_set_mana_ability_registry() -> ColorSetManaAbilityRegistry:
    registry = ColorSetManaAbilityRegistry(
        (
            ColorSetActivatedManaAbilityHandler(),
            ColorSetActivatedManaAbilityHandler(
                handler_id=MANA_HANDLER, family="ability.activated.mana.linked-hand-colors",
                rule_references=("106.1", "605.2", "605.3b", "607.2a", "607.3"),
                capability_dependencies=REQUIREMENTS,
            ),
            ColorSetActivatedManaAbilityHandler(
                handler_id=COMMANDER_IDENTITY_MANA_HANDLER_ID,
                family="ability.activated.mana.commander-identity",
                rule_references=("106.1", "605.1a", "605.3b", "903.4a", "903.4f"),
                capability_dependencies=("mana.activated.color_set", COMMANDER_IDENTITY_MANA_CAPABILITY),
            ),
            ColorSetActivatedManaAbilityHandler(
                handler_id=COMMANDER_TYPE_SCRY_MANA_HANDLER_ID,
                family="ability.activated.mana.commander-type-scry",
                rule_references=("106.6a", "601.2i", "603.7e", "605.3b", "903.3e"),
                capability_dependencies=("mana.activated.color_set", COMMANDER_IDENTITY_MANA_CAPABILITY,
                                         COMMANDER_TYPE_SCRY_CAPABILITY),
            ),
            ColorSetActivatedManaAbilityHandler(
                handler_id=COMMANDER_ENTRY_COUNTER_MANA_HANDLER_ID,
                family="ability.activated.mana.commander-entry-counters",
                rule_references=("106.6", "400.7h", "601.2h", "605.3b", "614.1c", "903.8"),
                capability_dependencies=("mana.activated.color_set", COMMANDER_IDENTITY_MANA_CAPABILITY,
                                         COMMANDER_ENTRY_COUNTER_MANA_CAPABILITY),
            ),
        )
    )
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


def color_set_mana_specs_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[ColorSetActivatedManaAbilitySpec, ...]:
    registry = default_color_set_mana_ability_registry()
    result: list[ColorSetActivatedManaAbilitySpec] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        result.extend(registry.lower(descriptor, None))
    return tuple(result)


__all__ = [
    "ColorSetActivatedManaAbilityHandler",
    "ColorSetManaAbilityRegistry",
    "color_set_mana_specs_from_descriptors",
    "default_color_set_mana_ability_registry",
]
