"""Lower a trusted opponent draw replacement to its current controller."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

from ..drawing.step_exemption import (
    DRAW_STEP_EXEMPT_REDIRECT_CAPABILITY,
    DRAW_STEP_EXEMPT_REDIRECT_HANDLER_ID,
)
from ..replacement import CreateResultDraws, ReplacementClass, ReplacementEffect
from .component_registry import exact_fields
from .context import SemanticNodeError

if TYPE_CHECKING:
    from .draw_replacements import DrawReplacementSourceContext


@dataclass(frozen=True, slots=True)
class DrawStepExemptRedirectHandler:
    handler_id: str = DRAW_STEP_EXEMPT_REDIRECT_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.draw.result_recipient"
    event: str = "draw"
    rule_references: tuple[str, ...] = ("121.6a", "121.7", "504.1", "614.5", "616.1", "616.2")
    capability_dependencies: tuple[str, ...] = (
        DRAW_STEP_EXEMPT_REDIRECT_CAPABILITY,
        "zone.draw.result_generated_ordering",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "condition", "modification"},
            field="draw redirect handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
            or descriptor["condition"] != {"affected_player_relation": "opponent"}
            or descriptor["modification"] != {"recipient": "source_controller"}
        ):
            raise SemanticNodeError("Draw redirection requires an opponent and the source controller")

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DrawReplacementSourceContext,
    ) -> ReplacementEffect:
        self.validate(descriptor)
        controller = context.source_controller
        if type(controller) is not str or not controller:
            raise SemanticNodeError("Draw redirection requires the current source controller")
        source_id = f"{context.source_object_id}@{context.source_zone_change_counter}"
        component = context.component_id or "printed"
        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{source_id}:{component}",
            source_id=source_id,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "affected_player": {"not_in": [controller]},
                "is_draw": {"eq": True},
            },
            operations=(CreateResultDraws(count=1, recipient=controller),),
            label=f"{context.source_ref}: {controller} draws instead",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DrawReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)
