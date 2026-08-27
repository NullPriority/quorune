from __future__ import annotations

"""Strict semantic lowering for fixed regeneration shield creation."""

from dataclasses import dataclass
from typing import Any, Mapping

from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .intents import CreateRegenerationShieldIntent, IntentPlan


@dataclass(frozen=True, slots=True)
class CreateRegenerationShieldHandler:
    handler_id: str = "generic.create-regeneration-shield.v2"
    schema_version: int = 2
    family: str = "effect.fixed-regeneration"
    operation: str = "regenerate"
    rule_references: tuple[str, ...] = (
        "506.4",
        "701.19",
        "701.19a",
        "701.19c",
    )
    capability_dependencies: tuple[str, ...] = (
        "permanent.regeneration.self_activation",
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
            family_label="Regeneration",
            allow_replacement_selections=False,
        )
        source = context.source
        source_logical_object_id = (
            source.logical_object_id
            if source is not None and source.card_ref == fields.object_ref
            else None
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                CreateRegenerationShieldIntent(
                    actor=context.actor,
                    object_ref=fields.object_ref,
                    logical_object_id=source_logical_object_id,
                    reason=fields.reason,
                ),
            ),
        )


REGENERATION_HANDLERS = (CreateRegenerationShieldHandler(),)


__all__ = [
    "CreateRegenerationShieldHandler",
    "REGENERATION_HANDLERS",
]
