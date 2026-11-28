"""Resolve a combined optional price and its retained public Mill choice."""

from dataclasses import replace

from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..replacement.immutable import FrozenMap
from ..rules.mill_paid_hand_return import (
    MILL_HAND_CHOICE_FIELD, mill_hand_choice, mill_hand_payment,
)
from ..semantic_runtime.intents import PayLifeIntent
from .fixed_effect_payment import prepare_fixed_payment_cost, complete_fixed_payment_cost
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _decode_payment(effect, actor, *, continuation=False):
    try:
        return mill_hand_payment(effect, actor=actor, continuation=continuation)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def prepare_mill_hand_payment(effect, context):
    actor = context.actor
    if actor != context.stack_controller or actor not in context.query.active_seats:
        raise SemanticChoiceError("Paid Mill return requires the resolving controller")
    spec, _cards = _decode_payment(effect, actor)
    mana = FixedEffectPaymentSpec("mana", requirements=FrozenMap(spec.mana_requirements()))
    if context.query.player_life_for_payment(actor) < spec.life:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The fixed life price is unavailable"),
        )
    prepared = prepare_fixed_payment_cost(mana, effect, context)
    if not prepared.request.public_context["payable"]:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The fixed mana price is unavailable"),
        )
    public = {**prepared.request.public_context, "life_payment": spec.life,
              "cost": spec.mana_requirements(), "payment_kind": "mana_life"}
    request = replace(prepared.request,
                      prompt=f"Pay {{{spec.generic}}} and {spec.life} life, or decline.",
                      public_context=FrozenMap(public))
    return replace(prepared, request=request)


def complete_mill_hand_payment(continuation, response, query):
    actor = continuation.semantic_frame.controller
    effect = continuation.effect
    spec, cards = _decode_payment(effect, actor, continuation=True)
    if effect["_choice_actor"] != actor or actor not in query.active_seats:
        raise SemanticChoiceError("Paid Mill return lost its original payer")
    mana = FixedEffectPaymentSpec("mana", requirements=FrozenMap(spec.mana_requirements()))
    completion = complete_fixed_payment_cost(mana, effect, response, query)
    if not completion.intents:
        return completion
    if query.player_life_for_payment(actor) < spec.life:
        raise SemanticChoiceError("The complete mana and life price is no longer payable")
    life = PayLifeIntent(actor=actor, player=actor, amount=spec.life, reason=effect["_stack_label"])
    result = FrozenMap({"op": "choose_option", "player": actor,
                        MILL_HAND_CHOICE_FIELD: tuple(card.to_dict() for card in cards)})
    return SemanticChoiceCompletion(intents=(*completion.intents, life), prepend_effects=(result,))


def _eligible_milled_cards(effect, *, actor, query):
    try:
        candidates = mill_hand_choice(effect, actor=actor)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    rows = []
    for candidate in candidates:
        row = query.object(candidate.ref, zones=(candidate.zone,))
        if (row is not None and row.object_id == candidate.object_id and row.owner == actor
                and row.logical_object_id == candidate.logical_object_id and row.known_to_actor
                and row.physical_card and not row.token and not row.face_down):
            rows.append((candidate, row))
    return tuple(rows)


def prepare_mill_hand_choice(effect, context):
    actor = context.actor
    if actor != context.stack_controller or actor not in context.query.active_seats:
        raise SemanticChoiceError("Paid Mill return requires the resolving controller")
    rows = _eligible_milled_cards(effect, actor=actor, query=context.query)
    if not rows:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("No committed milled card is available"),
        )
    choice = ObjectChoice(
        field_name="card", legal_refs=tuple(row.ref for _, row in rows),
        zones=tuple(dict.fromkeys(row.zone for _, row in rows)),
        minimum=1, maximum=1, optional=False, owner_relation="actor",
    )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose one of the milled cards to put into your hand.",
            choice=choice,
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "choose_option",
                                      "objects": tuple({"id": row.ref, "name": row.printed_name}
                                                       for _, row in rows)}),
        ),
        continuation_effect=FrozenMap(effect),
    )


def complete_mill_hand_choice(continuation, response, query):
    actor = continuation.semantic_frame.controller
    if actor not in query.active_seats:
        raise SemanticChoiceError("Committed Mill return lost its selecting player")
    rows = _eligible_milled_cards(continuation.effect, actor=actor, query=query)
    selected = response.get("card")
    choice = next((candidate for candidate, _ in rows if candidate.ref == selected), None)
    if type(selected) is not str or choice is None or selected not in query.choice_candidate_refs():
        raise SemanticChoiceError("Choose one card from the current committed Mill result")
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
        "op": "move_if_in_zone", "card": choice.ref, "from": choice.zone,
        "expected_zone_change_counter": choice.zone_change_counter, "destination": "hand",
        "controller": actor,
    }),))
