from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..extra_combat_model import (
    EXTRA_COMBAT_FOLLOWUP_FIELD, ExtraCombatUntap,
    ScheduleExtraCombatIntent, decode_extra_combat_effect,
)
from ..object_predicate import ObjectQuerySpec
from ..public_tap_state_sets import resolve_public_tap_state_set
from ..tap_state import set_permanent_tapped
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan


_CONTROLLED_CREATURES = AffectedPermanentSetSpec(
    query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
    controller_relation=PermanentControllerRelation.ACTOR,
)


@dataclass(frozen=True, slots=True)
class ExtraCombatHandler:
    """Bind the closed follow-up on an existing untap operation."""

    handler_id: str = "generic.extra-combat-after-current.v1"

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        source = context.source
        if source is None:
            raise SemanticNodeError("Extra combat requires a resolving stack source")
        try:
            untap = decode_extra_combat_effect(effect, resolved=True)
            card_ref = effect[EXTRA_COMBAT_FOLLOWUP_FIELD].get("source_card")
            if card_ref is not None and card_ref != source.card_ref:
                raise ValueError("Extra combat untap must retain its source reference")
            intent = ScheduleExtraCombatIntent(
                actor=context.query.require_active_seat(context.actor),
                source_ref=source.card_ref or source.stack_ref,
                reason=context.default_reason,
                untap=untap,
                permanent_ref=card_ref,
                logical_object_id=source.logical_object_id if card_ref is not None else None,
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(effect["op"], self.handler_id, (intent,))


def execute_extra_combat_intent(sink: Any, intent: ScheduleExtraCombatIntent) -> None:
    sink.turn_steps.require_current_combat()
    if intent.untap is ExtraCombatUntap.CONTROLLED_CREATURES:
        resolve_public_tap_state_set(
            sink, actor=intent.actor, spec=_CONTROLLED_CREATURES, tapped=False,
            reason=intent.reason, source_ref=intent.source_ref,
        )
    elif intent.permanent_ref is not None:
        set_permanent_tapped(
            sink, intent.permanent_ref, actor=intent.actor, tapped=False,
            reason=intent.reason, logical_object_id=intent.logical_object_id,
        )
    sink.turn_steps.schedule_extra_combat(intent.actor, source=intent.source_ref)
