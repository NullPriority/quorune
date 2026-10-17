from __future__ import annotations

"""Pure fixed Emerge cost component for the existing cost registry."""

from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..emerge_model import EMERGE_CAPABILITY, EMERGE_HANDLER, FixedManaEmergeSpec
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedManaEmergeHandler:
    handler_id: str = EMERGE_HANDLER
    schema_version: int = 1
    family: str = EMERGE_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('601.2b', '601.2f', '601.2h', '702.119a', '702.119c')
    capability_dependencies: tuple[str, ...] = (EMERGE_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'emerge'}, field='fixed Emerge cost')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Emerge cost identity changed')
        try:
            return FixedManaEmergeSpec.from_dict(descriptor['emerge'])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
