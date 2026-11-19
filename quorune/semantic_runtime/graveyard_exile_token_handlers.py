"""Lower one direct graveyard target and its fixed token result."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..graveyard_exile_token_model import (
    GRAVEYARD_EXILE_TOKEN_BASE_DEPENDENCIES,
    GRAVEYARD_EXILE_TOKEN_OPERATION,
    GraveyardExileTokenSpec,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .graveyard_exile_token_intent import GraveyardExileTokenIntent
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class GraveyardExileTokenHandler:
    handler_id: str = 'generic.exile-public-graveyard-card-for-token.v1'
    schema_version: int = 1
    family: str = 'effect.public-zone-move'
    operation: str = GRAVEYARD_EXILE_TOKEN_OPERATION
    rule_references: tuple[str, ...] = ('111.3', '111.4', '400.7j', '608.2b', '608.2c', '608.2h', '701.13a')
    capability_dependencies: tuple[str, ...] = GRAVEYARD_EXILE_TOKEN_BASE_DEPENDENCIES

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        fields = validate_direct_target_effect(effect, context, operation=self.operation, reference_field='card',
            family_label='Graveyard exile token', allow_replacement_selections=True,
            additional_allowed_fields=('result',))
        try:
            intent = GraveyardExileTokenIntent(
                actor=context.actor, object_ref=fields.object_ref, reason=fields.reason,
                replacement_selections=fields.replacement_selections, result=GraveyardExileTokenSpec.from_dict(effect['result']),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
