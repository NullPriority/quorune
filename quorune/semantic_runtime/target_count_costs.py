from __future__ import annotations

from dataclasses import dataclass

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..target_count_cost_model import (
    FixedManaPerTargetCostSpec, TARGET_COUNT_COST_CAPABILITY, TARGET_COUNT_COST_HANDLER,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedManaPerTargetCostHandler:
    handler_id: str = TARGET_COUNT_COST_HANDLER
    schema_version: int = 1
    family: str = TARGET_COUNT_COST_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('118.8', '601.2c', '601.2f', '601.2h')
    capability_dependencies: tuple[str, ...] = (TARGET_COUNT_COST_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'target_cost'}, field='Per-target mana cost')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Per-target mana cost identity changed')
        try:return FixedManaPerTargetCostSpec.from_dict(descriptor['target_cost'])
        except (KeyError, TypeError, ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
