from __future__ import annotations

"""Front-face declarations for fixed-mana back-face casting."""

from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..disturb_model import (
    DISTURB_CAPABILITY, DISTURB_HANDLER, FixedManaDisturbSpec,
    MORE_THAN_MEETS_THE_EYE_CAPABILITY, MORE_THAN_MEETS_THE_EYE_HANDLER,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


def _back_face_cast_spec(descriptor, handler, *, field, label):
    exact_fields(descriptor, {
        'handler_id', 'schema_version', 'event', REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, field,
    }, field=f'{label} declaration')
    if (descriptor['handler_id'] != handler.handler_id
            or type(descriptor['schema_version']) is not int
            or descriptor['schema_version'] != handler.schema_version
            or descriptor['event'] != handler.event
            or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
        raise SemanticNodeError(f'{label} identity or complete-card boundary changed')
    try:
        return FixedManaDisturbSpec.from_dict(descriptor[field])
    except (ValueError, TypeError) as exc:
        raise SemanticNodeError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class FixedManaDisturbHandler:
    handler_id: str = DISTURB_HANDLER
    schema_version: int = 1
    family: str = DISTURB_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('601.2b', '601.2f', '702.146a', '712.11d')
    capability_dependencies: tuple[str, ...] = (DISTURB_CAPABILITY,)

    def validate(self, descriptor):
        return _back_face_cast_spec(descriptor, self, field='disturb', label='Disturb')

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class MoreThanMeetsTheEyeHandler:
    handler_id: str = MORE_THAN_MEETS_THE_EYE_HANDLER
    schema_version: int = 1
    family: str = MORE_THAN_MEETS_THE_EYE_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('202.3b', '601.2b', '601.2f', '702.162')
    capability_dependencies: tuple[str, ...] = (MORE_THAN_MEETS_THE_EYE_CAPABILITY,)

    def validate(self, descriptor):
        return _back_face_cast_spec(descriptor, self, field='converted_cast', label='More Than Meets the Eye')

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
