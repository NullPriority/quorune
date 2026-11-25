"""Lower a trusted mandatory discard replacement for any affected player."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

from ..drawing.step_exemption import (
    DRAW_STEP_EXEMPT_DISCARD_CAPABILITY,
    DRAW_STEP_EXEMPT_DISCARD_HANDLER_ID,
)
from ..replacement import DiscardInsteadOfDraw, ReplacementClass, ReplacementEffect
from .component_registry import exact_fields
from .context import SemanticNodeError

if TYPE_CHECKING:
    from .draw_replacements import DrawReplacementSourceContext


@dataclass(frozen=True, slots=True)
class DrawStepExemptDiscardHandler:
    handler_id: str = DRAW_STEP_EXEMPT_DISCARD_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.draw.discard_result"
    event: str = "draw"
    rule_references: tuple[str, ...] = ("121.6a", "121.7", "504.1", "614.5", "616.1", "701.8")
    capability_dependencies: tuple[str, ...] = (
        DRAW_STEP_EXEMPT_DISCARD_CAPABILITY,
        "zone.draw.result_generated_ordering",
        "zone.discard.typed_cause",
        "zone.mill.fixed",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "condition", "modification"},
            field="discard draw replacement",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
            or descriptor["condition"] != {"affected_player_relation": "any"}
            or descriptor["modification"] != {"operation": "discard_instead_of_draw"}
        ):
            raise SemanticNodeError("Draw-discard replacements require the exact affected-player operation")

    def replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        context: DrawReplacementSourceContext,
    ) -> ReplacementEffect:
        self.validate(descriptor)
        source = f"{context.source_object_id}@{context.source_zone_change_counter}"
        component = context.component_id or "printed"
        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{source}:{component}",
            source_id=source,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={"is_draw": {"eq": True}},
            operations=(DiscardInsteadOfDraw(),),
            label=f"{context.source_ref}: discard before drawing or mill",
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: DrawReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)
