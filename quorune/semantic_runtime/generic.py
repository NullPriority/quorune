from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..drawing.model import (
    DiscardDrawnCardUnlessType,
    RevealDrawnCard,
    drawn_card_action_from_dict,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import (
    BecomeMonarchIntent,
    DrawCardsIntent,
    IntentPlan,
    MillCardsIntent,
)
from .nodes import (
    BecomeMonarchNode,
    DrawEachPlayerNode,
    DrawNode,
)


def _count(effect: Mapping[str, Any]) -> int:
    value = effect.get("count", 1)
    if type(value) is not int or value < 0:
        raise SemanticNodeError("Draw count must be a nonnegative integer")
    return value


def _reason(
    effect: Mapping[str, Any], context: ReadOnlyHandlerContext
) -> str:
    return str(effect.get("reason") or context.default_reason)


def _private(effect: Mapping[str, Any]) -> bool:
    value = effect.get("private", False)
    if type(value) is not bool:
        raise SemanticNodeError("Draw private flag must be a boolean")
    return value


@dataclass(frozen=True, slots=True)
class DrawHandler:
    handler_id: str = "generic.draw.v1"
    schema_version: int = 1
    family: str = "zone.draw"
    operation: str = "draw"
    rule_references: tuple[str, ...] = ("121.1", "121.2")
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        node = DrawNode(
            player=context.query.require_known_seat(
                str(effect.get("player") or context.actor)
            ),
            count=_count(effect),
            reason=_reason(effect, context),
            private=_private(effect),
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                DrawCardsIntent(
                    player=node.player,
                    count=node.count,
                    reason=node.reason,
                    private=node.private,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class MillHandler:
    handler_id: str = "generic.mill.v1"
    schema_version: int = 1
    family: str = "zone.mill"
    operation: str = "mill"
    rule_references: tuple[str, ...] = (
        "400.7",
        "701.17",
        "701.17a",
        "701.17b",
        "701.17c",
    )
    capability_dependencies: tuple[str, ...] = ("zone.mill.fixed",)

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        if set(effect) - {"op", "player", "count", "reason"}:
            raise SemanticNodeError("Mill effects have a closed schema")
        count = _count(effect)
        player = context.query.require_active_seat(
            str(effect.get("player") or context.actor)
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                MillCardsIntent(
                    actor=context.actor,
                    player=player,
                    count=count,
                    reason=_reason(effect, context),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class DrawEachPlayerHandler:
    handler_id: str = "generic.draw-each-player.v1"
    schema_version: int = 1
    family: str = "zone.draw"
    operation: str = "draw_each_player"
    rule_references: tuple[str, ...] = ("121.1", "121.2")
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        node = DrawEachPlayerNode(
            count=_count(effect),
            reason=_reason(effect, context),
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=tuple(
                DrawCardsIntent(
                    player=seat,
                    count=node.count,
                    reason=node.reason,
                    private=True,
                )
                for seat in context.query.apnap_order
            ),
            result_shape="by_player",
        )


@dataclass(frozen=True, slots=True)
class DrawWithActionsHandler:
    """Lower the closed CR 121.6c specifically-drawn-card action family."""

    handler_id: str = "generic.draw-with-actions.v1"
    schema_version: int = 1
    family: str = "zone.draw"
    operation: str = "draw_with_actions"
    rule_references: tuple[str, ...] = ("121.1", "121.6c")
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        allowed = {
            "op",
            "player",
            "count",
            "private",
            "reason",
            "post_draw_actions",
        }
        if set(effect) - allowed:
            raise SemanticNodeError(
                "Draw-with-actions carries unsupported fields"
            )
        if _count(effect) != 1:
            raise SemanticNodeError(
                "Draw-with-actions requires exactly one draw"
            )
        raw_actions = effect.get("post_draw_actions")
        if not isinstance(raw_actions, (list, tuple)) or any(
            not isinstance(value, Mapping) for value in raw_actions
        ):
            raise SemanticNodeError(
                "Draw-with-actions requires typed action objects"
            )
        try:
            actions = tuple(
                drawn_card_action_from_dict(value)
                for value in raw_actions
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if actions != (
            RevealDrawnCard(),
            DiscardDrawnCardUnlessType(card_type="land"),
        ):
            raise SemanticNodeError(
                "Draw-with-actions supports only public reveal then "
                "discard-unless-land"
            )
        player = context.query.require_known_seat(
            str(effect.get("player") or context.actor)
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                DrawCardsIntent(
                    player=player,
                    count=1,
                    reason=_reason(effect, context),
                    private=_private(effect),
                    post_draw_actions=actions,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class BecomeMonarchHandler:
    handler_id: str = "generic.become-monarch.v1"
    schema_version: int = 1
    family: str = "variant.monarch"
    operation: str = "become_monarch"
    rule_references: tuple[str, ...] = ("725.1", "725.2")
    capability_dependencies: tuple[str, ...] = (
        "variant.monarch.designate",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        node = BecomeMonarchNode(
            player=context.query.require_active_seat(
                str(effect.get("player") or context.actor)
            ),
            reason=_reason(effect, context),
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                BecomeMonarchIntent(
                    player=node.player,
                    reason=node.reason,
                ),
            ),
        )


GENERIC_HANDLERS = (
    DrawHandler(),
    MillHandler(),
    DrawEachPlayerHandler(),
    DrawWithActionsHandler(),
    BecomeMonarchHandler(),
)
