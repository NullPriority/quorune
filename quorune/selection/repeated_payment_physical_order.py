"""Use the physical ordering owner for a composed repeated-payment batch."""

from types import SimpleNamespace

from ..composition_batch_order_model import CompositionDepartureBatchRequest
from ..composition_departure_batch import prepare_composition_departure_batch
from ..composition_departure_commit import commit_prepared_composition_departures
from ..errors import GameRuleError
from ..semantic_choices.physical_ordering import physical_order_payload, physical_order_response
from ..semantic_runtime.zone_replacements import prepare_zone_change_replacement_batch


FIELD = "_payment_round_physical_order"


def _frame(request, orders):
    request.ordered_ids((*orders, *(refs for _, refs in request.choices[len(orders):])))
    return SimpleNamespace(request=request, orders=orders,
        current_choice=request.choices[len(orders)] if len(orders) < len(request.choices) else None)


def _offer(host, effect, resume, frame):
    (seat, _destination, _position), _refs = frame.current_choice
    host.permissions.issue(
        kind="permanent.composition_order", role="pilot", actors=[seat], allowed_actions=["order"],
        payload_by_actor={seat: physical_order_payload(frame)},
        continuation={FIELD: {"effect": effect, "resume": resume, "request": frame.request.to_dict(),
                              "orders": [list(value) for value in frame.orders]}},
    )


def commit_or_offer_payment_physical_order(owner, *, item, changes, kinds, origin, selections,
                                           reason, life, effect, resume):
    host = owner.host
    plans = prepare_zone_change_replacement_batch(
        host, changes, transition_kinds=kinds, selections=selections, discard_effect_origin=origin,
        error_type=GameRuleError,
    )
    batch = prepare_composition_departure_batch(
        owner, changes, reason=reason, actor=item.controller, transition_kinds=kinds,
        prepared_replacements=plans, discard_effect_origin=origin,
    )
    saved = effect.get(FIELD)
    if saved is not None:
        try:
            if CompositionDepartureBatchRequest.from_dict(saved["request"]) != batch.request:
                raise ValueError("Repeated payment's physical order changed")
            ordered = batch.with_orders(saved["orders"])
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
    elif batch.request.choices:
        _offer(host, effect, resume, _frame(batch.request, ()))
        return False
    else:
        ordered = batch.with_orders(())
    commit_prepared_composition_departures(owner, ordered, simultaneous_life=life)
    return True


def complete_payment_physical_order(host, decision):
    from .repeated_apnap_payment import begin_payment_round, require_resolving_payment_item

    try:
        state = decision.continuation[FIELD]
        if set(decision.continuation) != {FIELD} or set(state) != {"effect", "resume", "request", "orders"}:
            raise ValueError("Repeated payment physical order fields changed")
        effect, resume = state["effect"], state["resume"]
        require_resolving_payment_item(host, resume, effect)
        request = CompositionDepartureBatchRequest.from_dict(state["request"])
        frame = _frame(request, tuple(tuple(value) for value in state["orders"]))
        refs = physical_order_response(host, decision, frame)
        advanced = _frame(request, (*frame.orders, tuple(refs)))
        if advanced.current_choice is not None:
            _offer(host, effect, resume, advanced)
            return
        effect = {**effect, FIELD: {"request": request.to_dict(), "orders": [list(row) for row in advanced.orders]}}
        begin_payment_round(host, effect, resume)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
