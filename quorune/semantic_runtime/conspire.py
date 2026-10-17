from __future__ import annotations

from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..conspire_model import FixedConspireSpec, CONSPIRE_CAPABILITY, CONSPIRE_HANDLER
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedConspireHandler:
    handler_id: str = CONSPIRE_HANDLER
    schema_version: int = 1
    family: str = CONSPIRE_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('302.6', '601.2b', '601.2f', '601.2h', '702.78a', '702.78b')
    capability_dependencies: tuple[str, ...] = (CONSPIRE_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'conspire'}, field='Conspire declaration')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Conspire identity or complete-card boundary changed')
        try:
            return FixedConspireSpec.from_dict(descriptor['conspire'])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
