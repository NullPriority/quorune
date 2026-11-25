"""Source-pinned announced-X graveyard exile replacement on entry."""

from __future__ import annotations

from dataclasses import dataclass

from ..replacement import ReplacementClass, ReplacementEffect
from ..rules.linked_graveyard_entry import (
    LINKED_GRAVEYARD_ENTRY_DEPENDENCIES, LINKED_GRAVEYARD_ENTRY_HANDLER, linked_graveyard_entry_operation,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class LinkedGraveyardEntryHandler:
    handler_id: str = LINKED_GRAVEYARD_ENTRY_HANDLER
    schema_version: int = 1
    family: str = "replacement.zone.linked-graveyard-exile"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = ("107.3m", "122.6", "400.7", "607.2b", "614.1c", "614.12", "616.1")
    capability_dependencies: tuple[str, ...] = LINKED_GRAVEYARD_ENTRY_DEPENDENCIES

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event"}, field="Linked graveyard entry")
        if (type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != 1
                or descriptor["handler_id"] != self.handler_id or descriptor["event"] != self.event):
            raise SemanticNodeError("Linked graveyard entry handler identity changed")
        return self

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id, host, simultaneous_departures=()):
        self.validate(descriptor)
        if host is None or subject.destination != "battlefield" or subject.origin == "battlefield":
            return ()
        source = host.state.cards.get(subject.object_id)
        operation = linked_graveyard_entry_operation(
            host, source=source, controller=subject.destination_controller, component_id=component_id,
        )
        if operation is None:
            return ()
        return (ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}", source_id=subject.object_ref,
            event_kind=self.event, replacement_class=ReplacementClass.OTHER,
            conditions={"destination": {"eq": "battlefield"}, "object_ref": {"eq": subject.object_ref}},
            operations=(operation,), optional=True, label="Exile creature cards from your graveyard",
        ),)
