from __future__ import annotations

"""Intrinsic entry choices using the canonical replacement journal."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..entry_designations import (
    ENTRY_DESIGNATION_CAPABILITY,
    ENTRY_DESIGNATION_HANDLER_ID,
    LIMITED_CREATURE_TYPE_ENTRY_CAPABILITY,
    LIMITED_CREATURE_TYPE_ENTRY_HANDLER_ID,
    EntryDesignationKind,
    validate_designation,
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

    def _designation_values(self, descriptor, kind):
        return kind.values

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
            for value in self._designation_values(descriptor, kind)
        )


@dataclass(frozen=True, slots=True)
class LimitedCreatureTypeEntryDesignationHandler(EntryDesignationHandler):
    handler_id: str = LIMITED_CREATURE_TYPE_ENTRY_HANDLER_ID
    schema_version: int = 3
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY, LIMITED_CREATURE_TYPE_ENTRY_CAPABILITY)

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "designation", "options"},
                     field="limited creature-type entry designation")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["designation"] != "creature_type"):
            raise SemanticNodeError("Limited creature-type entry designation identity changed")
        options = descriptor["options"]
        if not isinstance(options, (list, tuple)) or len(options) < 2:
            raise SemanticNodeError("A limited creature-type choice requires a closed list")
        try:
            chosen = tuple(validate_designation(EntryDesignationKind.CREATURE_TYPE, value) for value in options)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if chosen != tuple(sorted(set(chosen))):
            raise SemanticNodeError("Limited creature-type choices must be ordered and unique")
        return EntryDesignationKind.CREATURE_TYPE

    def _designation_values(self, descriptor, kind):
        return tuple(descriptor["options"])
