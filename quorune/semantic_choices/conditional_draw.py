from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..public_history_query_model import (
    OPPONENT_CAST_COLOR_DRAW, SPELL_COLOR_HISTORY_FIELD, uses_sealed_opponent_cast_colors,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import DrawCardsIntent
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
)


def _colors(effect: Mapping[str, Any]) -> tuple[str, ...]:
    value = effect.get("colors")
    if not isinstance(value, (list, tuple)) or not value:
        raise SemanticChoiceError(
            "Conditional draw colors must be a nonempty list"
        )
    colors = tuple(str(color).upper() for color in value)
    if len(colors) != len(set(colors)) or any(
        color not in {"W", "U", "B", "R", "G"} for color in colors
    ):
        raise SemanticChoiceError(
            "Conditional draw colors must be unique Magic colors"
        )
    return colors


@dataclass(frozen=True, slots=True)
class OpponentCastColorDrawHandler:
    """Lower one public turn-history predicate to a canonical draw intent."""

    operation: str = OPPONENT_CAST_COLOR_DRAW
    handler_id: str = "choice.draw.opponent-cast-colors.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 608.2c",
        "CR 121.1",
        "CR 121.2",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )
    continuation_fields: tuple[str, ...] = ("player", "colors", SPELL_COLOR_HISTORY_FIELD)
    private_data: tuple[str, ...] = ("drawn card",)
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = (
        "DrawCardsIntent",
        "drawing.begin_draw_sequence",
    )
    replay_fixture: str = "conditional-opponent-color-draw"
    test_modules: tuple[str, ...] = (
        "tests.test_exact_zimone_closure",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        player = str(effect.get("player") or context.actor)
        if player not in context.query.active_seats:
            raise SemanticChoiceError(
                "Conditional draw player must still be in the game"
            )
        sealed = uses_sealed_opponent_cast_colors(effect)
        if sealed and (
            type(effect[SPELL_COLOR_HISTORY_FIELD]) is not int
            or effect[SPELL_COLOR_HISTORY_FIELD] != 1
            or set(effect) != {"op", "player", "colors", SPELL_COLOR_HISTORY_FIELD}
            or player != context.stack_controller or player != context.actor
        ):
            raise SemanticChoiceError("Sealed spell-color draw requires its exact controller and history version")
        colors = _colors(effect)
        matched = bool(
            set(colors).intersection(
                context.query.opponent_cast_colors_this_turn(player)
            )
        )
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(
                {"op": self.operation, "player": player, "colors": colors,
                 **({SPELL_COLOR_HISTORY_FIELD: 1} if sealed else {})}
            ),
            preparation_intents=(
                DrawCardsIntent(
                    player=player,
                    count=1,
                    reason=context.stack_label,
                    private=False,
                ),
            )
            if matched
            else (),
            auto_continue=AutoContinue(
                reason=(
                    "opponent cast a matching color this turn"
                    if matched
                    else "no opponent cast a matching color this turn"
                )
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        raise SemanticChoiceError(
            "Conditional opponent-color draw never issues a player choice"
        )


CONDITIONAL_DRAW_CHOICE_HANDLERS = (OpponentCastColorDrawHandler(),)


__all__ = [
    "CONDITIONAL_DRAW_CHOICE_HANDLERS",
    "OpponentCastColorDrawHandler",
]
