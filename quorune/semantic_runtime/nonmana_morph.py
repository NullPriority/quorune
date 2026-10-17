from __future__ import annotations

from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..nonmana_morph_model import FixedNonmanaMorphSpec, NONMANA_MORPH_CAPABILITY, NONMANA_MORPH_HANDLER
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedNonmanaMorphHandler:
    handler_id: str = NONMANA_MORPH_HANDLER
    schema_version: int = 1
    family: str = NONMANA_MORPH_CAPABILITY
    event: str = 'morph.action'
    rule_references: tuple[str, ...] = ('116.2b', '702.37a', '702.37e', '708.2a', '708.9')
    capability_dependencies: tuple[str, ...] = (NONMANA_MORPH_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
            'morph'}, field='fixed nonmana Morph')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Nonmana Morph source identity or complete-card boundary changed')
        try:
            return FixedNonmanaMorphSpec.from_dict(descriptor['morph'])
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
