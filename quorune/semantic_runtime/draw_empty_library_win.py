"""Compile one live controller's mandatory empty-library draw win."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from ..replacement import ReplacementClass, ReplacementEffect
from ..replacement.operations import WIN_INSTEAD_OF_EMPTY_DRAW, WinInsteadOfEmptyDraw
from ..rules.upkeep_game_win import PLAYER_WIN_CAPABILITY
from .context import SemanticNodeError

if TYPE_CHECKING:
    from .draw_replacements import DrawReplacementSourceContext


HANDLER_ID = "replacement.draw.result.win_empty_library.v1"
CAPABILITY = "zone.draw.replacement.controller_empty_library_win"
DEPENDENCIES = (CAPABILITY, "zone.draw.result_generated_ordering", PLAYER_WIN_CAPABILITY)


def empty_library_win_descriptor():
    return {
        "handler_id": HANDLER_ID, "schema_version": 1, "event": "draw",
        "requires_complete_card_program": True,
        "condition": {"affected_player_relation": "source_controller", "library_size": 0},
        "modification": {"operation": WIN_INSTEAD_OF_EMPTY_DRAW},
    }


@dataclass(frozen=True, slots=True)
class EmptyLibraryWinDrawReplacementHandler:
    handler_id: str = HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.draw.win_empty_library_result"
    event: str = "draw"
    rule_references: tuple[str, ...] = ("104.1", "104.2b", "121.6a", "614.6", "616.1")
    capability_dependencies: tuple[str, ...] = DEPENDENCIES

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        if (
            not isinstance(descriptor, Mapping)
            or type(descriptor.get("schema_version")) is not int
            or dict(descriptor) != empty_library_win_descriptor()
            or descriptor["requires_complete_card_program"] is not True
            or type(descriptor["condition"]["library_size"]) is not int
        ):
            raise SemanticNodeError("Empty-library win requires its exact mandatory controller draw replacement")

    def replacement_effect(
        self, descriptor: Mapping[str, Any], context: DrawReplacementSourceContext,
    ) -> ReplacementEffect:
        self.validate(descriptor)
        if not context.source_controller:
            raise SemanticNodeError("Empty-library win requires the current source controller")
        source = f"{context.source_object_id}@{context.source_zone_change_counter}"
        component = context.component_id or "printed"
        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{source}:{component}", source_id=source,
            event_kind=self.event, replacement_class=ReplacementClass.OTHER,
            conditions={
                "is_draw": {"eq": True}, "affected_player": {"eq": context.source_controller},
                "library_size": {"eq": 0},
            },
            operations=(WinInsteadOfEmptyDraw(),),
            label=f"{context.source_ref}: win the game instead of drawing",
        )

    def lower(
        self, descriptor: Mapping[str, Any], context: DrawReplacementSourceContext,
    ) -> tuple[ReplacementEffect, ...]:
        return (self.replacement_effect(descriptor, context),)
