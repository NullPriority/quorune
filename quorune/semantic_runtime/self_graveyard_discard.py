from __future__ import annotations

"""Registry-owned fixed additional hand-discard permission for a source card."""

from dataclasses import dataclass

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..rules.self_graveyard_discard import CAPABILITIES, EVENT, HANDLER_ID, SelfGraveyardDiscardSpec
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class SelfGraveyardDiscardPermissionHandler:
    handler_id: str = HANDLER_ID
    schema_version: int = 1
    family: str = 'permission.cast.self-zone'
    event: str = EVENT
    rule_references: tuple[str,...] = ('113.6f','118.8','601.2a','601.2f','601.2h','701.9a')
    capability_dependencies: tuple[str,...] = CAPABILITIES

    def validate(self, descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','source_zone','payment',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD},field='Paid self-graveyard permission')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=1 or descriptor['event']!=self.event
                or descriptor['source_zone']!='graveyard' or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Paid self-graveyard permission identity, origin or admission changed')
        try:return SelfGraveyardDiscardSpec.from_dict(descriptor['payment'])
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
