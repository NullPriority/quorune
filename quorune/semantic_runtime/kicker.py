from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..kicker import (
    FixedKickedEntrySpec,
    FixedManaKickerSpec,
    KICKED_ENTRY_CAPABILITY_ID,
    KICKED_ENTRY_EVENT,
    KICKED_ENTRY_HANDLER_ID,
    KICKER_CAPABILITY_ID,
    KICKER_CAST_OPTION_ID,
    KICKER_COST_HANDLER_ID,
    KICKER_RUNTIME_EVENT,
    KickerError,
)
from ..replacement.model import ReplacementClass, ReplacementEffect
from ..replacement.operations import (
    CreateAffectedObjectCounter,
    GrantAffectedObjectKeyword,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError
from .zone_replacement_model import (
    ZoneChangeReplacementContext,
    ZoneChangeSubjectSnapshot,
    ZoneDestinationIntent,
)


@dataclass(frozen=True, slots=True)
class FixedManaKickerHandler:
    handler_id: str = KICKER_COST_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.kicker.fixed_mana"
    event: str = KICKER_RUNTIME_EVENT
    rule_references: tuple[str, ...] = ("601.2f", "702.33", "702.33a", "702.33b")
    capability_dependencies: tuple[str, ...] = (KICKER_CAPABILITY_ID,)

    def validate(self, descriptor: Mapping[str, Any]) -> FixedManaKickerSpec:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
                "kicker",
            },
            field="fixed-mana Kicker handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Kicker handler identity changed")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError("Unsupported Kicker handler schema version")
        if descriptor["event"] != self.event:
            raise SemanticNodeError("Kicker handler event changed")
        if descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True:
            raise SemanticNodeError("Kicker requires complete-card admission")
        value = descriptor["kicker"]
        if not isinstance(value, Mapping):
            raise SemanticNodeError("Kicker descriptor must be an object")
        try:
            return FixedManaKickerSpec.from_dict(value)
        except KickerError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedManaKickerSpec, ...]:
        del context
        return (self.validate(descriptor),)


class FixedManaKickerRegistry(RuntimeComponentRegistry[object, FixedManaKickerSpec]):
    pass


@lru_cache(maxsize=1)
def default_fixed_mana_kicker_registry() -> FixedManaKickerRegistry:
    from .nonmana_kicker import FixedNonmanaKickerHandler
    registry = FixedManaKickerRegistry((FixedManaKickerHandler(),FixedNonmanaKickerHandler()))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


@dataclass(frozen=True, slots=True)
class FixedKickedEntryHandler:
    handler_id: str = KICKED_ENTRY_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.kicked_entry"
    event: str = KICKED_ENTRY_EVENT
    rule_references: tuple[str, ...] = (
        "122.6",
        "614.1c",
        "614.12",
        "614.16",
        "616.1",
        "702.33d",
    )
    capability_dependencies: tuple[str, ...] = (KICKED_ENTRY_CAPABILITY_ID,)

    def validate(self, descriptor: Mapping[str, Any]) -> FixedKickedEntrySpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "entry"},
            field="fixed kicked-entry handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Kicked-entry handler identity changed")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError("Unsupported kicked-entry schema version")
        if descriptor["event"] != self.event:
            raise SemanticNodeError("Kicked entry must handle zone changes")
        value = descriptor["entry"]
        if not isinstance(value, Mapping):
            raise SemanticNodeError("Kicked-entry descriptor must be an object")
        try:
            return FixedKickedEntrySpec.from_dict(value)
        except KickerError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> tuple[ZoneDestinationIntent, ...]:
        del context
        self.validate(descriptor)
        return ()

    def subject_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> ReplacementEffect:
        spec = self.validate(descriptor)
        if subject.destination_controller is None:
            raise SemanticNodeError("Kicked entry requires a destination controller")
        if not component_id:
            raise SemanticNodeError("Kicked entry requires component identity")
        operations: list[Any] = [
            CreateAffectedObjectCounter(
                counter_name="+1/+1",
                amount=spec.counter_amount,
                placing_player=subject.destination_controller,
                source_ref=subject.object_ref,
                sequence=0,
            )
        ]
        if spec.keyword is not None:
            operations.append(
                GrantAffectedObjectKeyword(keyword=spec.keyword, sequence=1)
            )
        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}",
            source_id=subject.object_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.SELF_REPLACEMENT,
            conditions={
                "destination": {"eq": "battlefield"},
                "object_ref": {"eq": subject.object_ref},
                "cast_option": {"eq": KICKER_CAST_OPTION_ID},
            },
            operations=tuple(operations),
            label=(
                f"{subject.object_ref}: kicked entry with "
                f"{spec.counter_amount} +1/+1 counter(s)"
            ),
        )


__all__ = [
    "default_fixed_mana_kicker_registry",
    "FixedKickedEntryHandler",
    "FixedManaKickerHandler",
    "FixedManaKickerRegistry",
]
