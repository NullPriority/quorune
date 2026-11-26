from __future__ import annotations

"""Lower and commit a game-duration grant to the resolving player."""

from dataclasses import dataclass

from ..continuous_effect_state import commit_continuous_effect
from ..game_hand_size_model import (
    CAPABILITY,
    OPERATION,
    NoMaximumForGameIntent,
    ResolvedNoMaximumHandSize,
    require_no_maximum_for_game_instruction,
)
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class NoMaximumForGameHandler:
    operation: str = OPERATION
    handler_id: str = "generic.grant-controller-no-maximum-hand-size.v1"
    schema_version: int = 1
    family: str = CAPABILITY
    rule_references: tuple[str, ...] = ("113.8", "514.1", "611.2a", "613.11")
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)

    def lower(self, effect, context):
        if context.source is None:
            raise SemanticNodeError("A game-duration hand-size grant requires its resolving source")
        context.query.require_active_seat(context.actor)
        try:
            require_no_maximum_for_game_instruction(effect, player=context.actor)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        intent = NoMaximumForGameIntent(context.actor, context.source.stack_ref)
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))


def commit_no_maximum_for_game(host, intent: NoMaximumForGameIntent) -> ResolvedNoMaximumHandSize:
    item = next((value for value in host.state.stack if value.ref == intent.stack_ref), None)
    if item is None or item.controller != intent.controller or item.context.get("currently_resolving") is not True:
        raise SemanticNodeError("The resolving source of the game-duration hand-size grant changed")
    host._require_seat(intent.controller, in_game=True)
    rule = ResolvedNoMaximumHandSize(
        host._next_ref("CE"), item.ref, host._next_zone_timestamp(), intent.controller,
    )
    commit_continuous_effect(host.state, rule)
    host._log(
        rule.controller, "cleanup.maximum.changed",
        f"{rule.controller} has no maximum hand size for the rest of the game.",
        {"player": rule.controller, "effect": rule.effect_id, "stack": rule.source_id},
        importance=2, changed_players=[rule.controller],
    )
    return rule
