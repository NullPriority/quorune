"""Lower a single revalidated graveyard target with a closed result recipe."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..rules.graveyard_exile_type_outcomes import (
    GRAVEYARD_EXILE_TYPE_OUTCOME_BASE_DEPENDENCIES,
    GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION,
    GraveyardExileTypeOutcomeSpec,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .graveyard_exile_type_outcome_intent import GraveyardExileTypeOutcomeIntent
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class GraveyardExileTypeOutcomeHandler:
    handler_id: str = 'generic.exile-public-graveyard-card-for-type-outcomes.v1'
    schema_version: int = 1
    family: str = 'effect.public-zone-move'
    operation: str = GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION
    rule_references: tuple[str, ...] = ('400.7j', '406.3a', '608.2b', '608.2c', '608.2h', '701.13a')
    capability_dependencies: tuple[str, ...] = GRAVEYARD_EXILE_TYPE_OUTCOME_BASE_DEPENDENCIES

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        fields = validate_direct_target_effect(effect, context, operation=self.operation, reference_field='card',
            family_label='Graveyard exile type outcome', allow_replacement_selections=True,
            additional_allowed_fields=('result',))
        try:
            intent = GraveyardExileTypeOutcomeIntent(
                actor=context.actor, object_ref=fields.object_ref, reason=fields.reason,
                replacement_selections=fields.replacement_selections, result=GraveyardExileTypeOutcomeSpec.from_dict(effect['result']),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
