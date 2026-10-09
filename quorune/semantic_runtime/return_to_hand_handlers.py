from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .context import ReadOnlyHandlerContext
from .direct_target_fields import validate_direct_target_effect
from .intents import (
    IntentPlan,
    ReturnGraveyardCardToOwnerHandIntent,
    ReturnPermanentToOwnerHandIntent,
)


@dataclass(frozen=True, slots=True)
class ReturnPermanentToOwnerHandHandler:
    handler_id: str = "generic.return-permanent-to-owner-hand.v1"
    schema_version: int = 1
    family: str = "effect.permanent-return"
    operation: str = "bounce"
    rule_references: tuple[str, ...] = (
        "108.3",
        "110.2",
        "400.2",
        "400.3",
        "400.6",
        "400.7",
        "608.2c",
    )
    capability_dependencies: tuple[str, ...] = (
        "permanent.return.owner_hand",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Return-to-hand",
            allow_replacement_selections=True,
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                ReturnPermanentToOwnerHandIntent(
                    actor=context.actor,
                    object_ref=fields.object_ref,
                    reason=fields.reason,
                    replacement_selections=fields.replacement_selections,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class ReturnGraveyardCardToOwnerHandHandler:
    handler_id: str = "generic.return-graveyard-card-to-owner-hand.v1"
    schema_version: int = 1
    family: str = "effect.graveyard-card-return"
    operation: str = "return_graveyard_card_to_owner_hand"
    rule_references: tuple[str, ...] = (
        "108.3",
        "400.2",
        "400.3",
        "400.7",
        "608.2b",
        "608.2c",
    )
    capability_dependencies: tuple[str, ...] = (
        "card.return.own_graveyard_to_owner_hand",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        if effect.get('schema_version') == 2:
            from ..event_card_return import event_card_return_intent
            from .context import SemanticNodeError
            try:
                intent = event_card_return_intent(effect, actor=context.actor, reason=context.default_reason)
            except (ValueError, TypeError, KeyError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Graveyard-card return",
            allow_replacement_selections=True,
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                ReturnGraveyardCardToOwnerHandIntent(
                    actor=context.actor,
                    object_ref=fields.object_ref,
                    reason=fields.reason,
                    replacement_selections=fields.replacement_selections,
                ),
            ),
        )


RETURN_TO_HAND_HANDLERS = (
    ReturnPermanentToOwnerHandHandler(),
    ReturnGraveyardCardToOwnerHandHandler(),
)


__all__ = [
    "RETURN_TO_HAND_HANDLERS",
    "ReturnGraveyardCardToOwnerHandHandler",
    "ReturnPermanentToOwnerHandHandler",
]
