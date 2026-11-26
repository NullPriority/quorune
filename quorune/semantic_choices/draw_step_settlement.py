from __future__ import annotations

"""Resolve each chosen Sylvan Library hand incarnation separately."""

from collections.abc import Mapping

from ..fixed_hand_putback_model import FixedHandPutbackOrder
from ..replacement.immutable import FrozenMap
from ..rules.draw_step_drawing_settlement import DRAW_STEP_SETTLEMENT_VERSION_FIELD
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent, PayLifeIntent
from .model import (
    AutoContinue,
    ObjectChoice,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_BASE = frozenset({"op", "player", DRAW_STEP_SETTLEMENT_VERSION_FIELD})
_ELIGIBLE_FIELD = "_eligible_drawn_hand"
_SELECTED_FIELD = "_ordered_drawn_hand"
_LIFE_PAYMENT = 4
_OPERATION = "sylvan_library_settle"


def _base(effect, actor):
    if (
        not _BASE <= set(effect)
        or effect["op"] != _OPERATION
        or effect["player"] != actor
        or type(effect[DRAW_STEP_SETTLEMENT_VERSION_FIELD]) is not int
        or effect[DRAW_STEP_SETTLEMENT_VERSION_FIELD] != 1
    ):
        raise SemanticChoiceError("Invalid draw-step settlement instruction")
    return {key: effect[key] for key in _BASE}


def _drawn_hand(query, actor):
    drawn = query.drawn_hand_incarnations_this_turn(actor)
    if drawn is None:
        raise SemanticChoiceError("Draw-step settlement requires authoritative draw history")
    by_incarnation = {row.logical_object_id: row for row in query.objects(zones=("hand",), owner=actor)}
    return {by_incarnation[value].ref: by_incarnation[value] for value in drawn if value in by_incarnation}


def _remaining(effect, hand):
    ordered = effect[_SELECTED_FIELD]
    if not isinstance(ordered, tuple) or not 1 <= len(ordered) <= 2:
        raise SemanticChoiceError("Draw-step settlement requires one or two ordered cards")
    refs = []
    for row in ordered:
        if not isinstance(row, Mapping) or set(row) != {"ref", "incarnation"}:
            raise SemanticChoiceError("Draw-step settlement order lost its selected identity")
        ref = row["ref"]
        if type(ref) is not str or ref not in hand or hand[ref].logical_object_id != row["incarnation"]:
            raise SemanticChoiceError("A selected draw is no longer the same hand object")
        refs.append(ref)
    if len(set(refs)) != len(refs):
        raise SemanticChoiceError("Draw-step settlement requires distinct selected cards")
    return ordered


def _visible_cards(rows):
    return [{"id": row.ref, "name": row.printed_name} for row in rows]


def _options(query, actor):
    if query.player_life_for_payment(actor) >= _LIFE_PAYMENT:
        return ("pay_life", "top")
    return ("top",)


def prepare_draw_step_settlement(effect, context):
    actor = context.actor
    base = _base(effect, actor)
    if actor != context.stack_controller or actor not in context.query.active_seats:
        raise SemanticChoiceError("Only the active trigger controller settles their drawn cards")
    hand = _drawn_hand(context.query, actor)
    public_context = {"stack": context.stack_ref, "operation": _OPERATION}
    if _SELECTED_FIELD in effect:
        if set(effect) != _BASE | {_SELECTED_FIELD}:
            raise SemanticChoiceError("Draw-step settlement has unrecognized continuation fields")
        remaining = _remaining(FrozenMap(effect), hand)
        current = hand[remaining[0]["ref"]]
        values = _options(context.query, actor)
        choice = ScalarChoice("choice", legal_values=values)
        prompt = (
            f"Pay 4 life or put {current.printed_name} on top of your library?"
            if "pay_life" in values else f"Put {current.printed_name} on top of your library."
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=prompt, choice=choice,
                public_context=FrozenMap({**public_context, "objects": _visible_cards((current,))}),
            ),
            continuation_effect=FrozenMap(effect),
        )
    if set(effect) != _BASE:
        raise SemanticChoiceError("Draw-step settlement has unrecognized selection fields")
    count = min(2, len(hand))
    if not count:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(base),
            auto_continue=AutoContinue("no eligible drawn cards"),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"Choose {count} card(s) drawn this turn in the order you want to settle them.",
            choice=ObjectChoice(
                "cards", tuple(hand), ("hand",), minimum=count, maximum=count,
                visibility="actor_private", owner_relation="actor",
                schema_extras=FrozenMap({"order": "first_to_last"}),
            ),
            public_context=FrozenMap({**public_context, "objects": _visible_cards(hand.values())}),
        ),
        continuation_effect=FrozenMap({
            **base, _ELIGIBLE_FIELD: {ref: row.logical_object_id for ref, row in hand.items()},
        }),
    )


def _select(continuation, base, hand, response):
    eligible = continuation.effect[_ELIGIBLE_FIELD]
    if not isinstance(eligible, Mapping) or {ref: row.logical_object_id for ref, row in hand.items()} != eligible:
        raise SemanticChoiceError("The controller's eligible drawn hand changed")
    refs = response.get("cards")
    if (
        not isinstance(refs, (list, tuple))
        or len(refs) != min(2, len(eligible))
        or any(type(ref) is not str or ref not in eligible for ref in refs)
        or len(set(refs)) != len(refs)
    ):
        raise SemanticChoiceError("Choose the required distinct drawn cards in settlement order")
    ordered = tuple({"ref": ref, "incarnation": eligible[ref]} for ref in refs)
    return SemanticChoiceCompletion(repeat_effect=FrozenMap({**base, _SELECTED_FIELD: ordered}))


def complete_draw_step_settlement(continuation, response, query):
    actor = continuation.semantic_frame.controller
    effect = continuation.effect
    base = _base(effect, actor)
    if actor not in query.active_seats:
        raise SemanticChoiceError("The draw-step settlement controller is unavailable")
    hand = _drawn_hand(query, actor)
    if set(effect) == _BASE | {_ELIGIBLE_FIELD}:
        return _select(continuation, base, hand, response)
    if set(effect) != _BASE | {_SELECTED_FIELD}:
        raise SemanticChoiceError("Invalid draw-step settlement continuation")
    remaining = _remaining(effect, hand)
    choice = response.get("choice")
    if type(choice) is not str or choice not in _options(query, actor):
        raise SemanticChoiceError("Choose an available action for the current drawn card")
    ref = remaining[0]["ref"]
    if choice == "pay_life":
        intent = PayLifeIntent(actor=actor, player=actor, amount=_LIFE_PAYMENT, reason="Sylvan Library")
    else:
        intent = MoveObjectsSimultaneouslyIntent(
            actor, (ref,), ("hand",), "library", "return selected draw to the library",
            owned_only=True, library_order=FixedHandPutbackOrder(actor, "top", (ref,)),
        )
    repeat = FrozenMap({**base, _SELECTED_FIELD: remaining[1:]}) if len(remaining) > 1 else None
    return SemanticChoiceCompletion(intents=(intent,), repeat_effect=repeat)
