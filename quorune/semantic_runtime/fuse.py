from __future__ import annotations

"""Fuse declaration in the existing ability component registry."""

from dataclasses import dataclass
from ..rules.fuse_model import FuseSpec, FUSE_HANDLER, FUSE_CAPABILITY
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FuseAbilityHandler:
    handler_id: str = FUSE_HANDLER
    schema_version: int = 1
    family: str = 'ability.static.fuse'
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str, ...] = ('702.102a', '702.102b', '702.102c', '702.102d', '709.4')
    capability_dependencies: tuple[str, ...] = (FUSE_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'fuse',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD}, field='Fuse declaration')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Fuse declaration identity is malformed')
        try:
            return FuseSpec.from_dict(descriptor['fuse'])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()
