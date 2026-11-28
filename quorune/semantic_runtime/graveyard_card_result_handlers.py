"""Lower one closed direct-target exile and conditional result recipe."""
from dataclasses import dataclass
from typing import Mapping, Any

from ..graveyard_card_result_model import (
    GRAVEYARD_CARD_COUNTER_LIFE_OPERATION, GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES, graveyard_card_counter_recipe,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .graveyard_card_result_intent import GraveyardCardCounterLifeIntent
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class GraveyardCardCounterLifeHandler:
    handler_id: str = 'generic.exile-graveyard-card-counter-life-result.v1'
    schema_version: int = 1
    family: str = 'effect.public-zone-move'
    operation: str = GRAVEYARD_CARD_COUNTER_LIFE_OPERATION
    rule_references: tuple[str, ...] = ('119.3', '122.1', '400.7j', '608.2b', '608.2c', '608.2h', '701.13a')
    capability_dependencies: tuple[str, ...] = GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        fields = validate_direct_target_effect(effect, context, operation=self.operation, reference_field='card',
            family_label='Targeted graveyard card result', allow_replacement_selections=True,
            additional_allowed_fields=('result',))
        if context.source is None:
            raise SemanticNodeError('Targeted source-counter result requires its original semantic source frame')
        try:
            value = effect['result']
            result = graveyard_card_counter_recipe(value).from_dict(value)
            intent = GraveyardCardCounterLifeIntent(actor=context.actor, object_ref=fields.object_ref,
                reason=fields.reason, replacement_selections=fields.replacement_selections,
                source_object_id=context.source.object_id, source_logical_object_id=context.source.logical_object_id,
                result=result)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
