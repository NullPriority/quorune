"""Select per-kind quantities on the same two original public objects."""

from ..counter_snapshot import permanent_counter_snapshot
from ..fixed_counter_movement_model import FIXED_COUNTER_MOVEMENT_OPERATION
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.counter_movement_intent import MoveCounterSetIntent
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


QUANTITIES_FIELDS = ("offered_counter_amounts", "selected_counter_amounts")
_OFFERED, _SELECTED = QUANTITIES_FIELDS
_IDENTITIES = ("from_identity", "to_identity")
_RESPONSE_FIELDS = {"amount", "action", "action_id", "a", "cap", "reason", "plan",
                    "next", "choice_schema", "label"}


def _live_counter_amounts(effect, query):
    donor = query.object(effect["from_ref"], zones=("battlefield",))
    recipient = query.object(effect["to_ref"], zones=("battlefield",))
    if (donor is None or recipient is None or donor.object_id == recipient.object_id
            or donor.phased_out or recipient.phased_out
            or donor.logical_object_id != effect["from_identity"]
            or recipient.logical_object_id != effect["to_identity"]
            or donor.controller != effect["player"] or recipient.controller != effect["player"]):
        return FrozenMap()
    try:
        return permanent_counter_snapshot(donor.counters)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _amount_offer(effect, query):
    offered = effect.get(_OFFERED)
    selected = effect.get(_SELECTED)
    if (not isinstance(offered, FrozenMap) or not offered or not isinstance(selected, FrozenMap)
            or offered != _live_counter_amounts(effect, query)
            or tuple(selected) != tuple(offered)[:len(selected)]
            or any(type(amount) is not int or not 0 <= amount <= offered[kind]
                   for kind, amount in selected.items())
            or len(selected) >= len(offered)):
        raise SemanticChoiceError("The original counter quantity offer changed during resolution")
    return tuple(offered)[len(selected)], selected


def prepare_counter_movement_quantities(effect, pinned, context):
    resuming = any(key in effect for key in (*QUANTITIES_FIELDS, *_IDENTITIES))
    if resuming:
        if pinned is None or pinned != FrozenMap({key: value for key, value in effect.items() if key not in QUANTITIES_FIELDS}):
            raise SemanticChoiceError("The original counter movement targets changed")
        choice_effect = FrozenMap(effect)
    else:
        amounts = _live_counter_amounts(pinned, context.query) if pinned is not None else FrozenMap()
        if not amounts:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The original permanents have no counters to move"))
        choice_effect = FrozenMap({**pinned, _OFFERED: amounts, _SELECTED: {}})
    kind, _ = _amount_offer(choice_effect, context.query)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"How many {kind} counters do you want to move?",
            choice=ScalarChoice(field_name="amount", value_type="integer", minimum=0,
                                maximum=choice_effect[_OFFERED][kind]),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": FIXED_COUNTER_MOVEMENT_OPERATION,
                "source": pinned["from_ref"], "recipient": pinned["to_ref"],
            }),
        ),
        continuation_effect=choice_effect,
    )


def complete_counter_movement_quantities(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    stack = query.stack_object(continuation.stack_ref)
    if (stack is None or stack.controller != actor or set(response) - _RESPONSE_FIELDS
            or any(type(effect.get(key)) is not str or not effect[key] for key in _IDENTITIES)):
        raise SemanticChoiceError("The original controller owns the counter quantity selection")
    kind, selected = _amount_offer(effect, query)
    amount = response.get("amount")
    offered = effect[_OFFERED]
    if type(amount) is not int or not 0 <= amount <= offered[kind]:
        raise SemanticChoiceError("Choose a quantity in the original available range")
    selected = {**selected, kind: amount}
    if len(selected) < len(offered):
        return SemanticChoiceCompletion(repeat_effect=FrozenMap({**effect, _SELECTED: selected}))
    amounts = FrozenMap({name: count for name, count in selected.items() if count})
    if not amounts:
        return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(intents=(MoveCounterSetIntent(
        actor=actor, from_ref=effect["from_ref"], from_identity=effect["from_identity"],
        to_ref=effect["to_ref"], to_identity=effect["to_identity"], amounts=amounts,
        reason="Move the selected counters", source_ref=continuation.stack_ref,
    ),))
