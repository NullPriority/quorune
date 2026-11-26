"""Intrinsic entry alternative that sets the game's initial day designation."""

from dataclasses import dataclass

from ..day_night_model import DAY_ENTRY_CAPABILITY, DAY_ENTRY_FIELD, DAY_ENTRY_HANDLER, day_entry_descriptor
from ..replacement_effects import ReplacementClass, ReplacementEffect, SetField
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class DayNightEntryHandler:
    handler_id: str = DAY_ENTRY_HANDLER
    schema_version: int = 1
    family: str = "replacement.zone.entry_state"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = ("614.1c", "614.12", "731.1a")
    capability_dependencies: tuple[str, ...] = (DAY_ENTRY_CAPABILITY,)

    def validate(self, descriptor):
        if type(descriptor.get("schema_version")) is not int or dict(descriptor) != day_entry_descriptor():
            raise SemanticNodeError("Initial day requires its exact intrinsic entry replacement.")
        return True

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id, host, simultaneous_departures):
        self.validate(descriptor)
        if (host is None or host.state.day_night is not None or subject.destination != "battlefield"
                or subject.destination_controller is None):
            return ()
        return (day_night_entry_effect(subject, component_id),)


def day_night_entry_effect(subject, component_id):
    return ReplacementEffect(
        effect_id=f"{DAY_ENTRY_HANDLER}:{subject.object_ref}:{component_id}", source_id=subject.object_ref,
        event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
        conditions={"destination":{"eq":"battlefield"}, "object_ref":{"eq":subject.object_ref}, DAY_ENTRY_FIELD:{"eq":None}},
        operations=(SetField(DAY_ENTRY_FIELD, True),), label="It becomes day as this permanent enters",
    )
