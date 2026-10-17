from __future__ import annotations

from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..replicate_model import REPLICATE_CAPABILITY, REPLICATE_HANDLER, FixedManaReplicateSpec
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedManaReplicateHandler:
    handler_id: str = REPLICATE_HANDLER
    schema_version: int = 1
    family: str = REPLICATE_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('601.2b', '601.2f', '601.2h', '702.56a', '702.56b')
    capability_dependencies: tuple[str, ...] = (REPLICATE_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'replicate'}, field='Replicate declaration')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Replicate identity or complete-card boundary changed')
        try:
            return FixedManaReplicateSpec.from_dict(descriptor['replicate'])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
