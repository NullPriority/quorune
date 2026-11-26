from __future__ import annotations

"""Scope the current spell controller's private whole-hand payment."""

from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.whole_hand_counter_payment import HAND_PAYMENT_SNAPSHOT, validate_whole_hand_counter_effect
from ..semantic_runtime.intents import CounterStackIntent, MoveObjectsSimultaneouslyIntent
from ..zone_trigger_events import ZoneTransitionKind
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _snapshot(query, actor):
    rows = query.objects(zones=("hand",), owner=actor)
    if any(row.zone != "hand" or row.owner != actor or not row.known_to_actor
           or not row.ref or not row.object_id or not row.logical_object_id for row in rows):
        raise SemanticChoiceError("The payer's current private hand identity is incomplete")
    if len(rows) != len({row.ref for row in rows}):
        raise SemanticChoiceError("Whole-hand payment requires distinct current cards")
    return tuple(sorted((row.ref, row.object_id, row.logical_object_id) for row in rows))


def _validate_payer(effect, query, actor, stack_ref, countering_controller):
    target_ref = effect.get("stack")
    try:
        validate_whole_hand_counter_effect(effect, target=target_ref, actor=actor)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    source = query.stack_object(stack_ref)
    target = query.stack_object(target_ref, exclude_ref=stack_ref)
    if (actor not in query.active_seats or source is None or source.controller != countering_controller
            or source.targets != (target_ref,) or target is None or target.controller != actor):
        raise SemanticChoiceError("Only the current targeted spell controller can pay their entire hand")


def prepare_whole_hand_counter_payment(effect, context):
    if effect.get("stack") is None:
        try:
            validate_whole_hand_counter_effect(effect, target=None, actor=effect.get("player"))
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="The targeted spell is no longer available"),
        )
    actor = context.actor
    _validate_payer(effect, context.query, actor, context.stack_ref, context.stack_controller)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Discard your hand to prevent this spell from being countered?",
            choice=ScalarChoice(field_name="pay", legal_values=(True, False)),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": effect["op"], "target_stack": effect["stack"],
            }),
        ),
        continuation_effect=FrozenMap({
            **dict(effect), HAND_PAYMENT_SNAPSHOT: _snapshot(context.query, actor),
            "_stack_label": context.stack_label,
        }),
    )


def complete_whole_hand_counter_payment(continuation, response, query):
    effect = thaw_value(continuation.effect)
    stored = effect.pop(HAND_PAYMENT_SNAPSHOT, None)
    label = effect.pop("_stack_label", None)
    actor = effect.get("player")
    source_controller = continuation.semantic_frame.controller
    _validate_payer(effect, query, actor, continuation.stack_ref, source_controller)
    current = _snapshot(query, actor)
    if stored != thaw_value(current) or type(label) is not str or not label:
        raise SemanticChoiceError("The private hand changed before the optional payment")
    pay = response.get("pay")
    if type(pay) is not bool:
        raise SemanticChoiceError("Choose whether to discard your entire hand")
    if not pay:
        return SemanticChoiceCompletion(intents=(CounterStackIntent(
            actor=actor, stack_ref=effect["stack"], reason=label, countered_by=source_controller,
            source_controller=source_controller,
        ),))
    if not current:
        return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(intents=(MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=tuple(row[0] for row in current), expected_zones=("hand",),
        destination="graveyard", reason=label, transition_kind=ZoneTransitionKind.DISCARD,
        owned_only=True,
    ),))
