from __future__ import annotations

"""Registry-owned intrinsic graveyard payment metadata; no mutation authority."""

from dataclasses import dataclass

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..rules.graveyard_cast_costs import (
    PaidGraveyardCastSpec, PAID_GRAVEYARD_HANDLER, PAID_GRAVEYARD_CAPABILITY,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class PaidGraveyardCastPermissionHandler:
    handler_id: str = PAID_GRAVEYARD_HANDLER
    schema_version: int = 1
    family: str = 'permission.cast.self-zone'
    event: str = 'cast.zone.permission'
    rule_references: tuple[str, ...] = ('113.6f', '118.8', '118.9', '601.2a', '601.2f', '601.2h')
    capability_dependencies: tuple[str, ...] = (PAID_GRAVEYARD_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'source_zone', 'payment',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD}, field='Paid intrinsic graveyard permission')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor['source_zone'] != 'graveyard'
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Paid graveyard permission identity, origin or complete-source admission changed')
        try:
            return PaidGraveyardCastSpec.from_dict(descriptor['payment'])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
