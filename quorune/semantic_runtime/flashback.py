from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..flashback import (
    FixedManaFlashbackSpec,
    FlashbackError,
    FLASHBACK_CAPABILITY_ID,
    FLASHBACK_HANDLER_ID,
    FLASHBACK_RUNTIME_EVENT,
)
from ..harmonize import DEPENDENCIES, EVENT, HANDLER_ID, HarmonizeSpec, harmonize_handler_descriptor
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedManaFlashbackHandler:
    handler_id: str = FLASHBACK_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.flashback.fixed_mana"
    event: str = FLASHBACK_RUNTIME_EVENT
    rule_references: tuple[str, ...] = (
        "601.2b",
        "601.2f",
        "702.34",
        "702.34a",
    )
    capability_dependencies: tuple[str, ...] = (FLASHBACK_CAPABILITY_ID,)

    def validate(self, descriptor: Mapping[str, Any]) -> FixedManaFlashbackSpec:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
                "flashback",
            },
            field="fixed-mana Flashback handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError(
                "Flashback handler identity, version, or event changed"
            )
        if descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True:
            raise SemanticNodeError("Flashback requires complete-card admission")
        try:
            return FixedManaFlashbackSpec.from_dict(descriptor["flashback"])
        except FlashbackError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedManaFlashbackSpec, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class HarmonizeCastHandler:
    handler_id: str = HANDLER_ID
    schema_version: int = 1
    family: str = "casting.harmonize.fixed_announced_x"
    event: str = EVENT
    rule_references: tuple[str, ...] = ("107.3a", "601.2b", "601.2f", "702.180a", "702.180b")
    capability_dependencies: tuple[str, ...] = DEPENDENCIES

    def validate(self, descriptor):
        try:
            spec = HarmonizeSpec.from_dict(descriptor["harmonize"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (dict(descriptor) != harmonize_handler_descriptor(spec)
                or type(descriptor["schema_version"]) is not int
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError("Harmonize requires its exact current graveyard cast descriptor")
        return spec

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)


class FixedManaFlashbackRegistry(
    RuntimeComponentRegistry[object, FixedManaFlashbackSpec | HarmonizeSpec]
):
    pass


@lru_cache(maxsize=1)
def default_fixed_mana_flashback_registry() -> FixedManaFlashbackRegistry:
    registry = FixedManaFlashbackRegistry((FixedManaFlashbackHandler(), HarmonizeCastHandler()))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


__all__ = [
    "default_fixed_mana_flashback_registry",
    "FixedManaFlashbackHandler",
    "FixedManaFlashbackRegistry",
]
