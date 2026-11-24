"""Apply and discover draw replacements in the shared duration journal."""

from __future__ import annotations

from dataclasses import dataclass

from ..continuous_effect_state import commit_continuous_effect
from ..draw_redirect_model import (
    DRAW_REDIRECT_CAPABILITY,
    DRAW_REDIRECT_OPERATION,
    ResolvedDrawRedirect,
    TemporaryDrawRedirectIntent,
)
from ..replacement import CreateResultDraws, PreventDraw, ReplacementClass, ReplacementEffect
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class TemporaryDrawRedirectHandler:
    operation: str = DRAW_REDIRECT_OPERATION
    handler_id: str = "generic.temporary-draw-redirect.v1"
    schema_version: int = 1
    family: str = DRAW_REDIRECT_CAPABILITY
    rule_references: tuple[str, ...] = ("121.6a", "121.7", "514.2", "611.2a", "614.5", "616.1")
    capability_dependencies: tuple[str, ...] = (DRAW_REDIRECT_CAPABILITY,)

    def lower(self, effect, context):
        if (
            set(effect) != {"op", "schema_version", "player"}
            or effect["op"] != self.operation
            or type(effect["schema_version"]) is not int
            or effect["schema_version"] != self.schema_version
            or context.source is None
        ):
            raise SemanticNodeError("Draw redirection requires its owned resolving source")
        try:
            intent = TemporaryDrawRedirectIntent(context.actor, effect["player"], context.source.stack_ref)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))


def apply_temporary_draw_redirect(host, intent: TemporaryDrawRedirectIntent):
    host._require_seat(intent.actor, in_game=True)
    host._require_seat(intent.target_player, in_game=True)
    effect = ResolvedDrawRedirect(
        host._next_ref("DR"),
        intent.source_id,
        host._next_zone_timestamp(),
        intent.actor,
        intent.target_player,
    )
    commit_continuous_effect(host.state, effect)
    host._log(
        intent.actor, "draw.replacement.created", "A temporary draw replacement applies.",
        {"effect_id": effect.effect_id, "target_player": effect.target_player}, importance=1,
    )
    return effect.effect_id


def collect_temporary_draw_redirects(host, player: str, active_seats):
    effects = []
    for rule in host.state.continuous_effects or ():
        if not isinstance(rule, ResolvedDrawRedirect) or rule.target_player != player:
            continue
        if rule.controller in active_seats:
            operation = CreateResultDraws(count=1, recipient=rule.controller)
            label = f"{rule.source_id}: {rule.controller} draws instead"
        else:
            operation = PreventDraw()
            label = f"{rule.source_id}: skip this draw"
        effects.append(ReplacementEffect(
            effect_id=f"{DRAW_REDIRECT_OPERATION}:{rule.effect_id}",
            source_id=rule.source_id,
            event_kind="draw",
            replacement_class=ReplacementClass.OTHER,
            conditions={"affected_player": {"eq": player}, "is_draw": {"eq": True}},
            operations=(operation,),
            label=label,
        ))
    return effects
