from __future__ import annotations

"""Offer one spell from a resolving draw instruction's public results."""

from collections.abc import Mapping

from ..discover_model import discover_options_fingerprint
from ..drawing.captured_hand import (
    DRAW_CAPTURE_FINISHED,
    DRAW_CAST_OFFER_FIELDS,
    CaptureDrawnHandCard,
    ResolveCapturedDrawCastIntent,
    RevealCapturedDrawsIntent,
    captured_draw_cast_instruction,
    captured_draw_cast_offer,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import DrawCardsIntent
from .model import (
    AutoContinue, CastOrDeclineChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def prepare_captured_draw_cast(effect, context):
    actor = context.actor
    if actor != context.stack_controller or actor not in context.query.active_seats:
        raise SemanticChoiceError("Draw and cast requires the active resolving controller")
    try:
        if "_captured" in effect:
            hand = captured_draw_cast_offer(effect, actor=actor)
            return _prepare_revealed_offer(effect, context, hand)
        count = captured_draw_cast_instruction(effect, actor=actor)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    draw = DrawCardsIntent(actor, count, context.stack_label,
                           post_draw_actions=(CaptureDrawnHandCard(context.stack_ref, count),))
    return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
        preparation_intents=(draw,), auto_continue=AutoContinue(
            reason="Draw the fixed cards before revealing the results.",
            prepend_effects=(FrozenMap({**effect, DRAW_CAPTURE_FINISHED: True}),),
        ))


def _prepare_revealed_offer(effect, context, hand):
    options = tuple(context.query.authorized_cast_options())
    reveal = RevealCapturedDrawsIntent(context.actor, context.stack_ref, hand, keep_revealed=bool(options))
    if not options:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            preparation_intents=(reveal,), auto_continue=AutoContinue(reason="The drawn cards offer no legal cast."))
    return SemanticChoicePreparation(request=SemanticChoiceRequest(
        prompt="Cast one of the revealed drawn cards without paying its mana cost?",
        choice=CastOrDeclineChoice(options),
        public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"], "cast_options": options})),
        continuation_effect=FrozenMap({**effect, "_options_fingerprint": discover_options_fingerprint(options)}),
        preparation_intents=(reveal,))


def _submitted_cast(response):
    choice = response.get("choice")
    nested = response.get("cast")
    flat = {key: value for key, value in response.items()
            if key not in {"action", "action_id", "cast", "choice", "choice_schema"}}
    if choice == "decline" and nested is None and not flat:
        return None
    if (choice != "cast" or nested is not None and not isinstance(nested, Mapping)
            or nested is not None and nested.keys() & flat.keys()):
        raise SemanticChoiceError("Choose one current drawn-card cast or decline")
    return FrozenMap({**(nested or {}), **flat})


def complete_captured_draw_cast(continuation, response, query):
    effect = continuation.effect
    if set(effect) != DRAW_CAST_OFFER_FIELDS | {"_options_fingerprint"}:
        raise SemanticChoiceError("The drawn-card cast continuation is malformed")
    actor = effect["player"]
    options = tuple(query.authorized_cast_options())
    fingerprint = effect["_options_fingerprint"]
    if actor not in query.active_seats or not options or discover_options_fingerprint(options) != fingerprint:
        raise SemanticChoiceError("The drawn-card casting options changed")
    try:
        hand = captured_draw_cast_offer({key: effect[key] for key in DRAW_CAST_OFFER_FIELDS}, actor=actor)
        intent = ResolveCapturedDrawCastIntent(actor, continuation.stack_ref, hand, fingerprint, _submitted_cast(response))
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(intents=(intent,))
