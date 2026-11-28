from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..extra_combat_model import (
    EXTRA_COMBAT_FOLLOWUP_FIELD, MAIN_PHASE_COMBAT_GRANT_FIELD, ExtraCombatUntap,
    ScheduleExtraCombatIntent, decode_extra_combat_effect,
)
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..rules.effect_node_capabilities import main_phase_extra_combat_program_dependencies
from ..public_tap_state_sets import resolve_public_tap_state_set
from ..tap_state import set_permanent_tapped
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan


_CONTROLLED_CREATURES = AffectedPermanentSetSpec(
    query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
    controller_relation=PermanentControllerRelation.ACTOR,
)
_ATTACKING_CREATURES = AffectedPermanentSetSpec(
    query=ObjectQuerySpec(
        zones=("battlefield",), types_all=("creature",), state_predicate=PermanentStatePredicateSpec(attacking=True),
    ),
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
            followup = effect[EXTRA_COMBAT_FOLLOWUP_FIELD]
            card_ref = followup.get("source_card", followup.get("exclude_source_card"))
            if card_ref is not None and card_ref != source.card_ref:
                raise ValueError("Extra combat untap must retain its source reference")
            main_phase = untap in {ExtraCombatUntap.GRANTED_COMBAT_CREATURES, ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR}
            intent = ScheduleExtraCombatIntent(
                actor=context.query.require_active_seat(context.actor),
                source_ref=source.card_ref or source.stack_ref,
                reason=context.default_reason,
                untap=untap,
                permanent_ref=card_ref,
                logical_object_id=source.logical_object_id if card_ref is not None else None,
                grant_stack_ref=source.stack_ref if main_phase else None,
                grant_source_object_id=source.object_id if main_phase and source.logical_object_id else None,
                grant_source_logical_id=source.logical_object_id if main_phase and source.object_id else None,
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(effect["op"], self.handler_id, (intent,))


def _schedule_main_phase_combat(sink, intent: ScheduleExtraCombatIntent) -> None:
    item = next((item for item in sink.state.stack if item.ref == intent.grant_stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != intent.actor
            or item.context.get("currently_resolving") is not True):
        raise SemanticNodeError("Main-phase combat needs its original resolving landfall trigger")
    program = sink.semantics.get(item.semantic_key)
    if program is None or not main_phase_extra_combat_program_dependencies(program):
        raise SemanticNodeError("Main-phase combat lost its original landfall program")
    sink.turn_steps.schedule_main_phase_combat(intent.actor, source=intent.source_ref, grant=item.ref)
    label = "Untap creatures at the beginning of the granted combat"
    sink.schedule_delayed_trigger(
        controller=intent.actor, label=label, event_kind="step.begin",
        condition={"phase": "combat", "step": "beginning_combat", "player": intent.actor,
                   MAIN_PHASE_COMBAT_GRANT_FIELD: item.ref},
        stack_template={"label": label, "context": {"dynamic_effects": [
            {"op": "set_public_tap_state", "source": None, "set": _CONTROLLED_CREATURES.to_dict(), "tapped": False},
        ]}},
        source_object_id=intent.grant_source_object_id, source_logical_object_id=intent.grant_source_logical_id,
        once=True, expires_turn_sequence=sink.state.turn_sequence,
    )


def _execute_activated_combat_and_main(sink, intent):
    from ..extra_combat_model import own_main_phase_for_combat
    from ..rules.controlled_creature_main_combat import controlled_main_combat_runtime_dependencies

    item = next((item for item in sink.state.stack if item.ref == intent.grant_stack_ref), None)
    if (item is None or item.kind not in {"activated_ability", "activated_ability_copy"} or item.controller != intent.actor
            or item.context.get("currently_resolving") is not True):
        raise SemanticNodeError("Additional combat and main need their original resolving activation")
    program = sink.semantics.get(item.semantic_key)
    if (program is None or not sink.semantic_program_is_current_trusted(program)
            or not controlled_main_combat_runtime_dependencies(program)):
        raise SemanticNodeError("Additional combat and main lost their original sorcery-timed activation")
    resolve_public_tap_state_set(
        sink, actor=intent.actor, spec=_CONTROLLED_CREATURES, tapped=False, reason=intent.reason, source_ref=intent.source_ref,
    )
    if own_main_phase_for_combat(sink.state, sink.state.active_player):
        sink.turn_steps.schedule_main_phase_combat(intent.actor, source=intent.source_ref, grant=item.ref, follow_with_main=True)


def execute_extra_combat_intent(sink: Any, intent: ScheduleExtraCombatIntent) -> None:
    if intent.untap is ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR:
        _execute_activated_combat_and_main(sink, intent)
        return
    if intent.untap is ExtraCombatUntap.GRANTED_COMBAT_CREATURES:
        _schedule_main_phase_combat(sink, intent)
        return
    conditional = intent.untap is ExtraCombatUntap.CONTROLLED_CREATURES_IF_OWN_COMBAT
    if not conditional:
        sink.turn_steps.require_current_combat()
    if intent.untap is not ExtraCombatUntap.SOURCE:
        excluded = None
        if intent.untap is ExtraCombatUntap.OTHER_CONTROLLED_CREATURES and intent.permanent_ref is not None:
            card = next((row for row in sink.state.cards.values() if row.ref == intent.permanent_ref), None)
            if card is not None and card.logical_object_id == intent.logical_object_id:
                excluded = card.ref
        spec = (_ATTACKING_CREATURES if intent.untap is ExtraCombatUntap.ATTACKING_CREATURES
                else replace(_CONTROLLED_CREATURES, exclude_source=excluded is not None))
        resolve_public_tap_state_set(
            sink, actor=intent.actor, spec=spec,
            tapped=False, reason=intent.reason, source_ref=excluded or intent.source_ref,
        )
    elif intent.permanent_ref is not None:
        set_permanent_tapped(
            sink, intent.permanent_ref, actor=intent.actor, tapped=False,
            reason=intent.reason, logical_object_id=intent.logical_object_id,
        )
    if not conditional or sink.turn_steps.in_current_own_combat(intent.actor):
        sink.turn_steps.schedule_extra_combat(intent.actor, source=intent.source_ref)
