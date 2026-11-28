"""Resume a committed-token sweep through the physical owners' orders."""

from collections.abc import Mapping
from dataclasses import replace

from ..affected_permanents import AffectedPermanentSetSpec
from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from ..object_predicate import ObjectQuerySpec
from ..ordered_incarnation_exclusion import OrderedIncarnationExclusion
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.announced_token_sweep import TOKEN_SWEEP_ORDER_FIELD, TOKEN_SWEEP_RESULT_FIELD
from ..semantic_runtime.intents import DestroyPermanentSetIntent
from .intent_replacement import serialized_replacement_selections
from .model import (
    AutoContinue, OrderingChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _choice(group):
    return OrderingChoice("cards", group.refs, visibility="public",
                          schema_extras=FrozenMap({"order": "bottom_to_top"}))


def _offer(effect, orders, selections):
    group = orders.current_group
    saved = FrozenMap({**effect, "player": group.owner, TOKEN_SWEEP_ORDER_FIELD: {
        "orders": orders.to_dict(), "replacement_selections": selections,
    }})
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Order the cards entering your graveyard from bottom to top.",
            choice=_choice(group),
            public_context=FrozenMap({"operation": effect["op"], "stack": effect[TOKEN_SWEEP_RESULT_FIELD]["stack_ref"]}),
        ),
        continuation_effect=saved,
    )


def _retained_order(effect):
    raw = effect.get(TOKEN_SWEEP_ORDER_FIELD)
    if raw is None and TOKEN_SWEEP_ORDER_FIELD not in effect:
        return None, ()
    if not isinstance(raw, Mapping) or set(raw) != {"orders", "replacement_selections"}:
        raise SemanticChoiceError("The destruction order lost its original transaction")
    return GraveyardArrivalOrders.from_dict(raw["orders"]), thaw_value(raw["replacement_selections"])


def prepare_token_sweep(effect, context):
    try:
        data = effect[TOKEN_SWEEP_RESULT_FIELD]
        orders, selections = _retained_order(effect)
        if orders is not None and orders.current_group is not None:
            return _offer(effect, orders, selections)
        from ..rules.destruction_result_model import DestructionFollowup
        from ..rules.gift_graveyard_spells import SWEEP_RESULT_VERSION

        gift = data["schema_version"] == SWEEP_RESULT_VERSION
        followup = DestructionFollowup.owned_creature_return() if gift and data["return_one"] else None
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="finish the ordered destruction" if gift else "finish the committed token sweep",
                                      committed_effects_from_result=0 if followup is not None else None), preparation_intents=(DestroyPermanentSetIntent(
            actor=data["controller"], reason="gift_ordered_destruction" if gift else "cast_x_token_sweep",
            source_ref=data["stack_ref"], followup=followup,
            spec=AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))),
            replacement_selections=selections,
            ordered_exclusion=OrderedIncarnationExclusion(data["protected"], orders),
        ),))
    except (TypeError, KeyError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def prepare_token_sweep_order(host, continuation, intent, required, *, index, count):
    group = required.orders.current_group
    if (index != 0 or count != 1 or not isinstance(intent, DestroyPermanentSetIntent)
            or intent.ordered_exclusion is None or intent.ordered_exclusion.orders is not None or group is None
            or continuation.semantic_frame.controller != intent.actor
            or TOKEN_SWEEP_RESULT_FIELD not in continuation.effect):
        raise SemanticChoiceError("Ordering requires the original protected destruction batch")
    return group.owner, _offer(continuation.effect, required.orders,
                               serialized_replacement_selections(intent.replacement_selections))


def complete_token_sweep(continuation, response, query):
    effect = dict(continuation.effect)
    try:
        orders, selections = _retained_order(effect)
        group = orders.current_group if orders is not None else None
        if (group is None or effect["player"] != group.owner or group.owner not in query.active_seats
                or set(response) - {"cards", "action", "action_id", "choice_schema"} or "cards" not in response
                or response.get("action", "choose") != "choose"
                or ("choice_schema" in response and FrozenMap(response["choice_schema"]) != FrozenMap(_choice(group).choice_schema()))):
            raise ValueError("The graveyard owner must submit the offered permutation")
        orders = orders.submit(group.owner, response["cards"])
        next_group = orders.current_group
        effect["player"] = next_group.owner if next_group is not None else effect[TOKEN_SWEEP_RESULT_FIELD]["controller"]
        effect[TOKEN_SWEEP_ORDER_FIELD] = {"orders": orders.to_dict(), "replacement_selections": selections}
    except (TypeError, KeyError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(repeat_effect=FrozenMap(effect))
