from __future__ import annotations

"""Pure Disturb declaration registered with the existing cast-cost catalog."""

from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..disturb_model import DISTURB_CAPABILITY, DISTURB_HANDLER, FixedManaDisturbSpec
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedManaDisturbHandler:
    handler_id: str = DISTURB_HANDLER
    schema_version: int = 1
    family: str = DISTURB_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('601.2b', '601.2f', '702.146a', '712.11d')
    capability_dependencies: tuple[str, ...] = (DISTURB_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'disturb'}, field='Disturb declaration')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Disturb identity or complete-card boundary changed')
        try:
            return FixedManaDisturbSpec.from_dict(descriptor['disturb'])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
