from __future__ import annotations

from dataclasses import dataclass

from ..rules.turn_cast_grant_model import (
    TURN_CAST_GRANT_CAPABILITY, TURN_CAST_GRANT_EVENT, TURN_CAST_GRANT_HANDLER, TurnCastGrantSpec,
    TURN_CAST_EXILE_CAPABILITY, TURN_CAST_EXILE_HANDLER, TURN_CAST_TAPPED_CAPABILITY, TURN_CAST_TAPPED_HANDLER,
    TURN_CAST_MILL_CAPABILITY, TURN_CAST_MILL_HANDLER, TURN_CAST_LIBRARY_SHARED_CAPABILITY, TURN_CAST_LIBRARY_SHARED_HANDLER,
    TURN_CAST_NONBATTLEFIELD_CAPABILITY,TURN_CAST_NONBATTLEFIELD_HANDLER,
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
    cast_rider: str = 'none'

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'requires_complete_card_program', 'grant'}, field='turn casting permission')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor['requires_complete_card_program'] is not True):
            raise SemanticNodeError('Turn casting grant requires its exact identity and complete source')
        try:
            spec = TurnCastGrantSpec.from_dict(descriptor['grant'])
            if spec.handler_id != self.handler_id:
                raise ValueError('Casting grant result does not match its registered owner')
            return spec
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)


def turn_cast_rider_handlers():
    return (
        TurnCastGrantHandler(handler_id=TURN_CAST_EXILE_HANDLER, family=TURN_CAST_EXILE_CAPABILITY,
            rule_references=('118.9', '400.7', '601.2i', '614.1a', '614.5', '616.1'),
            capability_dependencies=(TURN_CAST_GRANT_CAPABILITY, TURN_CAST_EXILE_CAPABILITY), cast_rider='graveyard_exile'),
        TurnCastGrantHandler(handler_id=TURN_CAST_TAPPED_HANDLER, family=TURN_CAST_TAPPED_CAPABILITY,
            rule_references=('118.9', '400.7', '601.2i', '614.1c', '614.12', '616.1'),
            capability_dependencies=(TURN_CAST_GRANT_CAPABILITY, TURN_CAST_TAPPED_CAPABILITY), cast_rider='tapped_entry'),
    )


def qualified_turn_grant_handlers():
    return (
        TurnCastGrantHandler(handler_id=TURN_CAST_NONBATTLEFIELD_HANDLER,family=TURN_CAST_NONBATTLEFIELD_CAPABILITY,
            rule_references=('113.6','400.7','601.2'),
            capability_dependencies=(TURN_CAST_GRANT_CAPABILITY,TURN_CAST_NONBATTLEFIELD_CAPABILITY)),
        TurnCastGrantHandler(handler_id=TURN_CAST_MILL_HANDLER,family=TURN_CAST_MILL_CAPABILITY,
            rule_references=('113.6','400.7','601.2','701.17a','701.17c'),
            capability_dependencies=(TURN_CAST_GRANT_CAPABILITY,TURN_CAST_MILL_CAPABILITY)),
        TurnCastGrantHandler(handler_id=TURN_CAST_LIBRARY_SHARED_HANDLER,family=TURN_CAST_LIBRARY_SHARED_CAPABILITY,
            rule_references=('113.6','305.1','305.2','400.7','601.2'),
            capability_dependencies=(TURN_CAST_GRANT_CAPABILITY,TURN_CAST_LIBRARY_SHARED_CAPABILITY)),
    )
