from __future__ import annotations

"""Intrinsic entry choices using the canonical replacement journal."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..entry_designations import (
    ENTRY_DESIGNATION_CAPABILITY,
    ENTRY_DESIGNATION_HANDLER_ID,
    EntryDesignationKind,
)
from ..replacement_effects import ReplacementClass, ReplacementEffect, SetField
from .component_registry import exact_fields
from .context import SemanticNodeError
from .zone_replacement_model import (
    ZoneChangeReplacementContext,
    ZoneChangeSubjectSnapshot,
    ZoneDestinationIntent,
)


@dataclass(frozen=True, slots=True)
class EntryDesignationHandler:
    handler_id: str = ENTRY_DESIGNATION_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.entry_designation"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "105.1", "205.3m", "400.7", "614.1c", "614.12", "614.12a", "616.1", "707.2",
    )
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> EntryDesignationKind:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "designation"},
            field="entry designation",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError("Entry designation handler identity changed")
        try:
            return EntryDesignationKind(descriptor["designation"])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError("Entry designation vocabulary is unsupported") from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> tuple[ZoneDestinationIntent, ...]:
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> tuple[ReplacementEffect, ...]:
        kind = self.validate(descriptor)
        if subject.destination_controller is None or not component_id:
            raise SemanticNodeError(
                "Intrinsic entry choice requires a destination controller and component"
            )
        return tuple(
            ReplacementEffect(
                effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}:{value}",
                source_id=subject.object_ref,
                event_kind=self.event,
                replacement_class=ReplacementClass.OTHER,
                conditions={
                    "destination": {"eq": "battlefield"},
                    "object_ref": {"eq": subject.object_ref},
                    kind.event_field: {"eq": None},
                },
                operations=(SetField(kind.event_field, value),),
                label=f"{subject.object_ref}: choose {kind.value.replace('_', ' ')} {value}",
            )
            for value in kind.values
        )
