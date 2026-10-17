from __future__ import annotations

"""A current source-local replacement, independent of its casting history."""

from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..replacement.model import ReplacementClass, ReplacementEffect
from ..replacement.operations import SetField
from ..self_graveyard_exile_model import SELF_GRAVEYARD_EXILE_CAPABILITY, SELF_GRAVEYARD_EXILE_HANDLER
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class SelfGraveyardExileHandler:
    handler_id: str = SELF_GRAVEYARD_EXILE_HANDLER
    schema_version: int = 1
    family: str = SELF_GRAVEYARD_EXILE_CAPABILITY
    event: str = 'zone.change'
    rule_references: tuple[str, ...] = ('113.6', '614.1a', '614.15', '616.1')
    capability_dependencies: tuple[str, ...] = (SELF_GRAVEYARD_EXILE_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD}, field='source graveyard exile')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Source graveyard-exile identity changed')

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id):
        self.validate(descriptor)
        if subject.destination != 'graveyard':
            return ()
        return (ReplacementEffect(
            effect_id=f'{self.handler_id}:{subject.logical_object_id}:{component_id}',
            source_id=subject.object_ref, event_kind='zone.change', replacement_class=ReplacementClass.OTHER,
            conditions={'destination': {'eq': 'graveyard'}, 'object_ref': {'eq': subject.object_ref},
                'logical_object_id': {'eq': subject.logical_object_id}},
            operations=(SetField('destination', 'exile'),),
            label=f'{subject.object_ref}: exile instead of putting this object in a graveyard'),)
