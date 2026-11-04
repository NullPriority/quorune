"""Source-local announced-cost reductions in the existing cast registry."""
from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..declared_cast_cost_model import DECLARED_CAST_COST_CAPABILITY
from ..declared_cost_reductions import (
    DECLARED_COST_REDUCTION_EVENT, DECLARED_COST_REDUCTION_HANDLER, DeclaredCostReductionSpec,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class DeclaredCostReductionHandler:
    handler_id: str = DECLARED_COST_REDUCTION_HANDLER
    schema_version: int = 1
    family: str = 'casting.cost.modifier.self_public'
    event: str = DECLARED_COST_REDUCTION_EVENT
    rule_references: tuple[str, ...] = ('118.7a', '118.8a', '601.2b', '601.2f', '702.166b', '702.166c')
    capability_dependencies: tuple[str, ...] = ('casting.cost.self_public_reduction', DECLARED_CAST_COST_CAPABILITY)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'reduction'}, field='Declared-cost reduction')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Declared-cost reduction identity or complete-card authority changed')
        try:
            return DeclaredCostReductionSpec.from_dict(descriptor['reduction'])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
