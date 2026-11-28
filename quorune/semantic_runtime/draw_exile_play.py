"""Lower the fixed mandatory controller draw replacement to its typed result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

from ..replacement import ReplacementClass, ReplacementEffect
from ..replacement.operations import EXILE_TURN_PLAY_DRAW_RESULT, ExileTopForTurnPlayInsteadOfDraw
from .context import SemanticNodeError

if TYPE_CHECKING:
    from .draw_replacements import DrawReplacementSourceContext


HANDLER_ID = "replacement.draw.result.exile_top_turn_play.v1"
CAPABILITY = "zone.draw.replacement.exile_top_turn_play"
DEPENDENCIES = (CAPABILITY, "zone.draw.result_generated_ordering", "zone.impulse_access.fixed")


def draw_exile_play_descriptor():
    return {
        "handler_id": HANDLER_ID, "schema_version": 1, "event": "draw",
        "condition": {"affected_player_relation": "source_controller"},
        "modification": {"operation": EXILE_TURN_PLAY_DRAW_RESULT},
    }


@dataclass(frozen=True, slots=True)
class DrawExilePlayReplacementHandler:
    handler_id: str = HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.draw.exile_turn_play_result"
    event: str = "draw"
    rule_references: tuple[str, ...] = ("121.2", "121.7", "614.1a", "614.6", "616.1", "400.7")
    capability_dependencies: tuple[str, ...] = DEPENDENCIES

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        if (not isinstance(descriptor, Mapping) or type(descriptor.get("schema_version")) is not int
                or dict(descriptor) != draw_exile_play_descriptor()):
            raise SemanticNodeError("Draw exile play requires its exact mandatory controller replacement")

    def replacement_effect(self, descriptor: Mapping[str, Any], context: DrawReplacementSourceContext) -> ReplacementEffect:
        self.validate(descriptor)
        if not context.source_controller:
            raise SemanticNodeError("Draw exile play requires the current source controller")
        source = f"{context.source_object_id}@{context.source_zone_change_counter}"
        component = context.component_id or "printed"
        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{source}:{component}", source_id=source,
            event_kind=self.event, replacement_class=ReplacementClass.OTHER,
            conditions={"is_draw": {"eq": True}, "player": {"eq": context.source_controller}},
            operations=(ExileTopForTurnPlayInsteadOfDraw(),),
            label=f"{context.source_ref}: exile the top cards with play permission",
        )

    def lower(self, descriptor: Mapping[str, Any], context: DrawReplacementSourceContext) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)
