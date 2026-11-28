"""Stage the public offer, then settle its two independently ordered batches."""

from ..replacement.immutable import FrozenMap
from ..rules.tempt_draw_token import (
    ANSWERS, ORDER, STAGE_FIELDS, draw_result, opponent_result_token_effect, token_result, validate_offer,
)
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _offer_state(effect, query, actor):
    try:
        validate_offer(effect, materialized=True)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if effect["beneficiary"] != actor or actor not in query.active_seats:
        raise SemanticChoiceError("Tempting offer lost its original resolving controller")
    current = tuple(seat for seat in query.apnap_order if seat != actor)
    if "player" not in effect:
        return current, ()
    order, answers = effect[ORDER], effect[ANSWERS]
    if (not isinstance(order, (list, tuple))
            or any(type(seat) is not str or seat == actor or seat not in query.seats for seat in order)
            or len(set(order)) != len(order)
            or tuple(seat for seat in order if seat in query.active_seats) != current
            or not isinstance(answers, (list, tuple)) or len(answers) >= len(order)
            or any(type(answer) is not str or answer not in ("accept", "decline", "unavailable") for answer in answers)
            or effect["player"] != order[len(answers)]):
        raise SemanticChoiceError("Tempting offer changed the sealed opponent order or its decisions")
    return tuple(order), tuple(answers)


def _advance(effect, order, answers, query):
    if len(answers) < len(order):
        return (FrozenMap({**effect, "player": order[len(answers)], ORDER: order, ANSWERS: answers}),)
    accepted = tuple(seat for seat, answer in zip(order, answers) if answer == "accept")
    if not accepted:
        return ()
    actor, token = effect["beneficiary"], effect["token"]
    active_acceptances = tuple(seat for seat in accepted if seat in query.active_seats)
    results = []
    if active_acceptances:
        results += [
            {"op": "draw_each_player", "players": active_acceptances, "count": 1, "private": True},
            opponent_result_token_effect(actor, token, active_acceptances),
        ]
    for _seat in accepted:
        results.extend((draw_result(actor), token_result(token, actor)))
    return tuple(FrozenMap(value) for value in results)


def prepare_tempt_draw_token(effect, context):
    order, answers = _offer_state(effect, context.query, context.stack_controller)
    continuation = FrozenMap(effect)
    if "player" not in effect:
        return SemanticChoicePreparation(request=None, continuation_effect=continuation,
            auto_continue=AutoContinue("Order the opponents' offers", _advance(effect, order, answers, context.query)))
    if effect["player"] != context.actor:
        raise SemanticChoiceError("Tempting offer was issued to the wrong opponent")
    if context.actor not in context.query.active_seats or not context.query.draw_permission(context.actor).allows_complete_draw(1):
        return SemanticChoicePreparation(request=None, continuation_effect=continuation,
            auto_continue=AutoContinue("This opponent cannot accept", _advance(effect, order, (*answers, "unavailable"), context.query)))
    previous = tuple({"player": seat, "choice": answer} for seat, answer in zip(order, answers) if answer != "unavailable")
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"Draw a card and create a {effect['token']['name']}?",
            choice=ScalarChoice("choice", ("accept", "decline")),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "offer_draw", "previous_choices": previous}),
        ), continuation_effect=continuation,
    )


def complete_tempt_draw_token(continuation, response, query):
    effect = continuation.effect
    order, answers = _offer_state(effect, query, continuation.semantic_frame.controller)
    choice = response.get("choice")
    fields = {"choice", "action", "action_id", "a", "cap", "reason", "plan", "next", "choice_schema", "label"}
    unexpected = set(response) - fields
    if unexpected:
        raise SemanticChoiceError("Tempting offer cannot change fields: " + ", ".join(sorted(unexpected)))
    if (choice not in ("accept", "decline") or not set(STAGE_FIELDS) <= effect.keys()
            or effect["player"] not in query.active_seats
            or (choice == "accept" and not query.draw_permission(effect["player"]).allows_complete_draw(1))):
        raise SemanticChoiceError("Tempting offer requires one legal answer from the current opponent")
    return SemanticChoiceCompletion(prepend_effects=_advance(effect, order, (*answers, choice), query))
