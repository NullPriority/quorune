from __future__ import annotations

"""One canonical color excluded from an intrinsic entry designation."""

from dataclasses import dataclass

from ..entry_designations import (
    ENTRY_COLOR_COMPOSITION_CAPABILITY, ENTRY_DESIGNATION_CAPABILITY,
    EXCLUDED_COLOR_ENTRY_HANDLER_ID, EntryDesignationKind, validate_designation,
)
from .component_registry import exact_fields
from .context import SemanticNodeError
from .entry_designations import EntryDesignationHandler


@dataclass(frozen=True, slots=True)
class ExcludedColorEntryDesignationHandler(EntryDesignationHandler):
    handler_id: str = EXCLUDED_COLOR_ENTRY_HANDLER_ID
    schema_version: int = 2
    family: str = 'replacement.zone.entry_color_exclusion'
    rule_references: tuple[str, ...] = ('105.1', '109.5', '400.7', '614.1c', '614.12', '614.12a', '616.1', '707.2')
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY, ENTRY_COLOR_COMPOSITION_CAPABILITY)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'designation', 'excluded_color'},
                     field='excluded-color entry designation')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version or descriptor['event'] != self.event
                or descriptor['designation'] != 'color'):
            raise SemanticNodeError('Excluded-color entry designation identity changed')
        try:
            validate_designation(EntryDesignationKind.COLOR, descriptor['excluded_color'])
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return EntryDesignationKind.COLOR

    def _designation_values(self, descriptor, kind):
        return tuple(value for value in kind.values if value != descriptor['excluded_color'])
