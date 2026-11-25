from __future__ import annotations

"""Strict lowering for resolution-created zone-object keyword grants."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..zone_object_keyword_model import (
    ZoneObjectKeywordGrantError,
    normalized_zone_object_keyword,
    validate_controlled_creature_indestructible,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .intents import GrantZoneObjectKeywordIntent, IntentPlan


@dataclass(frozen=True, slots=True)
class GrantZoneObjectKeywordHandler:
    handler_id: str = "generic.grant-zone-object-keyword.v1"
    schema_version: int = 1
    family: str = "effect.continuous-zone-object"
    operation: str = "grant_zone_object_keyword"
    rule_references: tuple[str, ...] = (
        "608.2c",
        "611.2a",
        "611.2c",
        "613.1f",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.resolution.fixed_keyword_zone_object",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        if "scope" in effect or "schema_version" in effect:
            try:
                validate_controlled_creature_indestructible(effect)
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            if context.source is None:
                raise SemanticNodeError("Zone-object keyword grants require resolving source context")
            return IntentPlan(operation=self.operation, handler_id=self.handler_id,
                intents=(GrantZoneObjectKeywordIntent(actor=context.actor, object_ref=None,
                    keyword="indestructible", source=context.source, reason=context.default_reason,
                    controlled_creature_set=True),))
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Zone-object keyword grant",
            allow_replacement_selections=False,
            additional_allowed_fields=("keyword",),
        )
        if context.source is None:
            raise SemanticNodeError(
                "Zone-object keyword grants require resolving source context"
            )
        try:
            keyword = normalized_zone_object_keyword(effect.get("keyword"))
        except ZoneObjectKeywordGrantError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                GrantZoneObjectKeywordIntent(
                    actor=context.actor,
                    object_ref=fields.object_ref,
                    keyword=keyword,
                    source=context.source,
                    reason=fields.reason,
                ),
            ),
        )


ZONE_OBJECT_KEYWORD_HANDLERS = (GrantZoneObjectKeywordHandler(),)


__all__ = [
    "GrantZoneObjectKeywordHandler",
    "ZONE_OBJECT_KEYWORD_HANDLERS",
]
