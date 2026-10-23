from __future__ import annotations

from dataclasses import dataclass

from ..rules.optional_life_cost_model import (
    OPTIONAL_LIFE_COST_CAPABILITY, OPTIONAL_LIFE_COST_EVENT, OPTIONAL_LIFE_COST_HANDLER,
    OptionalLifeColorReductionSpec,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class OptionalLifeColorReductionHandler:
    handler_id: str = OPTIONAL_LIFE_COST_HANDLER
    schema_version: int = 1
    family: str = 'casting.cost.optional_life_colored_reduction'
    event: str = OPTIONAL_LIFE_COST_EVENT
    rule_references: tuple[str, ...] = ('118.3b', '118.8', '118.8a', '601.2b', '601.2f', '601.2h')
    capability_dependencies: tuple[str, ...] = (OPTIONAL_LIFE_COST_CAPABILITY, 'casting.additional_cost.fixed_life_payment')

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'requires_complete_card_program', 'cost'},
            field='optional life colored reduction')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor['requires_complete_card_program'] is not True):
            raise SemanticNodeError('Optional life reduction requires its exact identity and complete source')
        try:
            return OptionalLifeColorReductionSpec.from_dict(descriptor['cost'])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
