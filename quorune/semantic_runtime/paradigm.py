"""Closed declaration of a resolving spell's first-resolution recurrence."""
from dataclasses import dataclass
from ..paradigm_model import (PARADIGM_CAPABILITY, PARADIGM_HANDLER,
    RESOLUTION_NAME_HISTORY_CAPABILITY, RETAINED_CARD_COPY_CAPABILITY)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class ParadigmSpellDeclarationHandler:
    handler_id: str = PARADIGM_HANDLER
    schema_version: int = 1
    family: str = 'casting.lifecycle.paradigm'
    event: str = 'resolve'
    rule_references: tuple[str, ...] = ('201.2a', '608.2b', '702.192a', '707.12')
    capability_dependencies: tuple[str, ...] = (PARADIGM_CAPABILITY,
        RESOLUTION_NAME_HISTORY_CAPABILITY, RETAINED_CARD_COPY_CAPABILITY)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event'}, field='Paradigm spell')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event):
            raise SemanticNodeError('Paradigm spell declaration identity is malformed')
        return descriptor

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()
