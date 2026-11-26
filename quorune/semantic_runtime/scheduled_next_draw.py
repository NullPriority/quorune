"""Bind the authored next-draw instruction to a resolving controller."""

from dataclasses import dataclass

from ..drawing.scheduled_draw_results import schedule_next_draw_replacement
from ..discard_effect_origin import DiscardEffectOrigin
from ..next_draw_replacement_model import (
    NEXT_DRAW_REPLACEMENT_CAPABILITY, NEXT_DRAW_REPLACEMENT_OPERATION, NextDrawResult, ResolvedNextDrawReplacement,
    ScheduleNextDrawResultIntent,
)
from .intents import IntentPlan
from .context import ReadOnlyHandlerContext, SemanticNodeError


def lower_next_draw_result(effect, context: ReadOnlyHandlerContext) -> ScheduleNextDrawResultIntent:
    if context.source is None:
        raise SemanticNodeError("A next-draw grant requires a resolving stack object")
    context.query.require_active_seat(context.actor)
    try:
        result = NextDrawResult.from_instruction(effect, player=context.actor)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return ScheduleNextDrawResultIntent(context.actor, context.source.stack_ref, result)


def apply_next_draw_result_intent(host, intent: ScheduleNextDrawResultIntent) -> ResolvedNextDrawReplacement:
    host._require_seat(intent.actor, in_game=True)
    item = next((candidate for candidate in host.state.stack if candidate.ref == intent.source_id), None)
    if item is None or item.controller != intent.actor:
        raise SemanticNodeError("The resolving source of the next-draw grant changed")
    origin = (
        DiscardEffectOrigin.from_resolving_stack(item)
        if intent.result.kind == "discard_each_opponent" and host.state.discard_effect_origin_version == 1
        else None
    )
    rule = ResolvedNextDrawReplacement(
        effect_id=host._next_ref("DR"),
        source_id=intent.source_id,
        timestamp=host._next_zone_timestamp(),
        controller=intent.actor,
        result=intent.result,
        source_ref=host._stack_source_ref(item),
        discard_origin=origin,
    )
    schedule_next_draw_replacement(host.state, rule)
    host._log(
        intent.actor,
        "draw.replacement.created",
        "A replacement applies to this controller's next draw this turn.",
        {"effect_id": rule.effect_id, "source": rule.source_id, "controller": rule.controller},
        importance=1,
    )
    return rule


@dataclass(frozen=True, slots=True)
class ScheduledNextDrawResultHandler:
    operation: str = NEXT_DRAW_REPLACEMENT_OPERATION
    handler_id: str = "generic.controller-next-draw-result.v1"
    schema_version: int = 1
    family: str = NEXT_DRAW_REPLACEMENT_CAPABILITY
    rule_references: tuple[str, ...] = ("121.2", "514.2", "611.2a", "614.5", "614.11", "616.1")
    capability_dependencies: tuple[str, ...] = (NEXT_DRAW_REPLACEMENT_CAPABILITY,)

    def lower(self, effect, context):
        intent = lower_next_draw_result(effect, context)
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
