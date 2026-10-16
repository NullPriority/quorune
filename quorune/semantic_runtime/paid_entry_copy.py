from __future__ import annotations

"""Pure validation/lowering of a source-pinned paid-copy cost component."""

from dataclasses import dataclass

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..paid_entry_copy_model import PaidEntryCopySpec, PAID_ENTRY_COPY_HANDLER, PAID_ENTRY_COPY_CAPABILITY
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class PaidEntryCopyCostHandler:
    handler_id: str = PAID_ENTRY_COPY_HANDLER
    schema_version: int = 1
    family: str = PAID_ENTRY_COPY_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('601.2f', '702.175a', '702.175b')
    capability_dependencies: tuple[str, ...] = (PAID_ENTRY_COPY_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'paid_copy'}, field='paid entry copy cost')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Paid entry copy cost identity changed')
        try:
            return PaidEntryCopySpec.from_dict(descriptor['paid_copy'])
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
