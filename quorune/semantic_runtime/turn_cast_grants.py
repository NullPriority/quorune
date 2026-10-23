from __future__ import annotations

from dataclasses import dataclass

from ..rules.turn_cast_grant_model import (
    TURN_CAST_GRANT_CAPABILITY, TURN_CAST_GRANT_EVENT, TURN_CAST_GRANT_HANDLER, TurnCastGrantSpec,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class TurnCastGrantHandler:
    handler_id: str = TURN_CAST_GRANT_HANDLER
    schema_version: int = 1
    family: str = 'casting.permission.source_once_per_turn'
    event: str = TURN_CAST_GRANT_EVENT
    rule_references: tuple[str, ...] = ('113.6', '118.8', '118.9', '400.7', '601.2a', '601.2b', '601.2f', '601.2i')
    capability_dependencies: tuple[str, ...] = (TURN_CAST_GRANT_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'requires_complete_card_program', 'grant'}, field='turn casting permission')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor['requires_complete_card_program'] is not True):
            raise SemanticNodeError('Turn casting grant requires its exact identity and complete source')
        try:
            return TurnCastGrantSpec.from_dict(descriptor['grant'])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
