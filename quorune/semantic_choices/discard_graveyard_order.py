"""Issue and complete the actual graveyard owner's order for a discard result."""

from collections.abc import Mapping

from ..discard_result_model import (
    DISCARD_GRAVEYARD_ORDER_FIELD, EVENT_PLAYER_DISCARD_RESULT_VERSION, SELECTED_LAND_DISCARD_RESULT_VERSION,
    DiscardWholeHandsResultIntent, ORDERED_OPPONENTS_DISCARD_VERSION, decode_discard_result_identity,
    is_ordered_opponents_discard, validate_discard_draw,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from .intent_replacement import serialized_replacement_selections
from .model import (
    OrderingChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def prepare_discard_graveyard_order(host, continuation, intent, required, *, index, count):
    group = required.orders.current_group
    version = continuation.effect.get("schema_version")
    ordered_opponents = version == ORDERED_OPPONENTS_DISCARD_VERSION
    if (not isinstance(intent, DiscardWholeHandsResultIntent) or index != 0 or count != 1
            or not intent.require_graveyard_order or intent.graveyard_orders is not None or group is None
            or (not ordered_opponents and (len(required.orders.groups) != 1 or intent.players != (group.owner,)))
            or (ordered_opponents and any(member.owner not in intent.players for member in required.orders.groups))
            or version not in (EVENT_PLAYER_DISCARD_RESULT_VERSION, SELECTED_LAND_DISCARD_RESULT_VERSION,
                               ORDERED_OPPONENTS_DISCARD_VERSION)
            or intent.selected_lands != (version == SELECTED_LAND_DISCARD_RESULT_VERSION)
            or continuation.semantic_frame.controller != intent.actor):
        raise SemanticChoiceError("Graveyard ordering requires the original event-player discard")
    objects = []
    for ref, identity in group.members:
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.ref != ref or card.logical_object_id != identity.logical_object_id
                or card.owner != group.owner or card.zone != "hand"):
            raise SemanticChoiceError("A retained discard order member changed before the offer")
        objects.append({"id": ref, "name": card.printed_name})
    saved = FrozenMap({
        **continuation.effect,
        DISCARD_GRAVEYARD_ORDER_FIELD: {
            "intent": intent.identity(),
            "orders": required.orders.to_dict(),
            "replacement_selections": serialized_replacement_selections(intent.replacement_selections),
        },
    })
    return group.owner, _order_offer(saved, group, objects, stack_ref=continuation.stack_ref)


def _order_offer(saved, group, objects, *, stack_ref):
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Order the cards entering your graveyard from bottom to top.",
            choice=OrderingChoice("cards", group.refs, visibility="actor_private",
                                  schema_extras=FrozenMap({"order": "bottom_to_top"})),
            public_context=FrozenMap({"operation": saved["op"], "stack": stack_ref,
                                      "objects": objects}),
        ),
        continuation_effect=saved,
    )


def prepare_next_opponents_discard_order(effect, context):
    try:
        retained = effect[DISCARD_GRAVEYARD_ORDER_FIELD]
        orders = GraveyardArrivalOrders.from_dict(retained["orders"])
        group = orders.current_group
        data = decode_discard_result_identity(retained["intent"])
        expected = tuple(p for p in context.query.apnap_order if p != effect["actor"])
        if (group is None or group.owner != context.actor or effect.get("player") != context.actor
                or not orders.choices or tuple(data["players"]) != expected
                or FrozenMap(data["hands"]) != context.query.discard_hand_inputs):
            raise ValueError("The next opponent's retained order changed")
        cards = {row.ref: row for row in context.query.objects(zones=("hand",), owner=context.actor)}
        if any(ref not in cards for ref in group.refs):
            raise ValueError("An ordered discard member is no longer visible to its owner")
        objects = [{"id": ref, "name": cards[ref].printed_name} for ref in group.refs]
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return _order_offer(FrozenMap(effect), group, objects, stack_ref=context.stack_ref)


def complete_discard_graveyard_order(continuation, response, query):
    effect = dict(continuation.effect)
    retained = effect.pop(DISCARD_GRAVEYARD_ORDER_FIELD, None)
    ordered_opponents = is_ordered_opponents_discard(effect)
    if ordered_opponents:
        effect.pop("player", None)
    try:
        validate_discard_draw(effect)
        if (effect["schema_version"] not in (EVENT_PLAYER_DISCARD_RESULT_VERSION, SELECTED_LAND_DISCARD_RESULT_VERSION,
                                              ORDERED_OPPONENTS_DISCARD_VERSION)
                or not isinstance(retained, Mapping)
                or set(retained) != {"intent", "orders", "replacement_selections"}):
            raise ValueError("Discard ordering lost its original instruction and choices")
        data = decode_discard_result_identity(retained["intent"])
        origin = getattr(query, "discard_effect_origin", None)
        if data.pop("discard_effect_origin", None) != (origin.to_dict() if origin is not None else None):
            raise ValueError("The resolving discard origin changed before graveyard ordering")
        expected_players = (tuple(p for p in query.apnap_order if p != effect["actor"]) if ordered_opponents
                            else tuple(effect["players"]))
        if (data["actor"] != continuation.semantic_frame.controller or data["actor"] != effect["actor"]
                or tuple(data["players"]) != expected_players
                or data.get("selected_lands", False) != (effect["schema_version"] == SELECTED_LAND_DISCARD_RESULT_VERSION)
                or not data.get("require_graveyard_order") or "graveyard_orders" in data
                or FrozenMap(data["hands"]) != query.discard_hand_inputs):
            raise ValueError("The original discard authority or retained hand membership changed")
        orders = GraveyardArrivalOrders.from_dict(retained["orders"])
        group = orders.current_group
        if (group is None or group.owner not in query.active_seats
                or (not ordered_opponents and (len(orders.groups) != 1 or orders.choices
                                               or tuple(data["players"]) != (group.owner,)))
                or (ordered_opponents and any(member.owner not in expected_players for member in orders.groups))):
            raise ValueError("The graveyard order must be completed by its surviving owner")
        if (set(response) - {"cards", "action", "action_id", "choice_schema"} or "cards" not in response
                or response.get("action", "choose") != "choose"):
            raise ValueError("Graveyard ordering requires exactly the offered permutation")
        if "choice_schema" in response:
            expected = OrderingChoice("cards", group.refs, visibility="actor_private",
                schema_extras=FrozenMap({"order": "bottom_to_top"})).choice_schema()
            if FrozenMap(response["choice_schema"]) != FrozenMap(expected):
                raise ValueError("Graveyard ordering response changed its issued schema")
        order = orders.submit(group.owner, response["cards"])
        if ordered_opponents and order.current_group is not None:
            return SemanticChoiceCompletion(repeat_effect=FrozenMap({
                **effect, "player": order.current_group.owner,
                DISCARD_GRAVEYARD_ORDER_FIELD: {**retained, "orders": order.to_dict()},
            }))
        intent = DiscardWholeHandsResultIntent(
            **data, graveyard_orders=order, replacement_selections=thaw_value(retained["replacement_selections"]),
            discard_effect_origin=origin,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(intents=(intent,))
