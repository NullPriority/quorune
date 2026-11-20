"""Lower a validated single announced target and an exclusive outcome recipe."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..graveyard_exile_branches_model import (
    GRAVEYARD_EXILE_BRANCH_BASE_DEPENDENCIES,
    GRAVEYARD_EXILE_BRANCH_OPERATION,
    GraveyardExileBranchSpec,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .graveyard_exile_branch_intent import GraveyardExileBranchIntent
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class GraveyardExileBranchHandler:
    handler_id: str = 'generic.exile-public-graveyard-card-branch.v1'
    schema_version: int = 1
    family: str = 'effect.public-zone-move'
    operation: str = GRAVEYARD_EXILE_BRANCH_OPERATION
    rule_references: tuple[str, ...] = ('400.7j', '406.3a', '608.2b', '608.2c', '608.2d', '608.2h', '701.13a')
    capability_dependencies: tuple[str, ...] = GRAVEYARD_EXILE_BRANCH_BASE_DEPENDENCIES

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        fields = validate_direct_target_effect(effect, context, operation=self.operation, reference_field='card',
            family_label='Exclusive graveyard exile', allow_replacement_selections=True,
            additional_allowed_fields=('result',))
        try:
            result = GraveyardExileBranchSpec.from_dict(effect['result'])
            if result.optional and context.source is None:
                raise ValueError('Optional exclusive exile needs its original stack source frame')
            source = context.source
            intent = GraveyardExileBranchIntent(
                actor=context.actor, object_ref=fields.object_ref, reason=fields.reason,
                replacement_selections=fields.replacement_selections, result=result,
                source_object_id=source.object_id if source else None,
                source_logical_object_id=source.logical_object_id if source else None,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
