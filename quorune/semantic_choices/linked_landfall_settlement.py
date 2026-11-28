"""Commit linked exile settlement and delegate any actual graveyard order."""
from collections.abc import Mapping
from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime.linked_landfall_settlement_intent import LinkedLandfallSettlementIntent
from ..rules.linked_landfall_play import EXPLORE_SETTLE_FIELD, exploration_links
from ..rules.committed_quantity_followups import CommittedQuantityFollowup
from .model import AutoContinue, OrderingChoice, SemanticChoiceError, SemanticChoiceCompletion, SemanticChoicePreparation, SemanticChoiceRequest


ORDER_FIELD="_linked_landfall_orders"


def _decode(effect):
    packed=effect.get(ORDER_FIELD)
    if packed is None:
        return None, ()
    if not isinstance(packed,Mapping) or set(packed)!={"orders","selections"}:
        raise ValueError("Malformed linked graveyard order")
    return GraveyardArrivalOrders.from_dict(packed["orders"]),thaw_value(packed["selections"])


def _offer(effect,orders,selections):
    group=orders.current_group
    saved=FrozenMap({**effect,"player":group.owner,ORDER_FIELD:{"orders":orders.to_dict(),"selections":selections}})
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(prompt="Order the cards entering your graveyard from bottom to top.",
            choice=OrderingChoice("cards",group.refs,visibility="public",schema_extras=FrozenMap({"order":"bottom_to_top"})),
            public_context=FrozenMap({"operation":effect["op"]})),
        continuation_effect=saved,
    )


def prepare_linked_settlement(effect,context):
    try:
        orders,selections=_decode(effect)
        if orders is not None and orders.current_group is not None:
            if context.actor!=orders.current_group.owner:
                raise ValueError("Only the current owner can order graveyard arrivals")
            return _offer(effect,orders,selections)
        refs=tuple(effect["_linked_settlement_refs"])
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("Commit linked graveyard settlement",committed_effects_from_result=0),
            preparation_intents=(LinkedLandfallSettlementIntent(
                actor=context.stack_controller,object_refs=refs,expected_zones=("exile",),destination="graveyard",
                reason=context.stack_label,damage_source=FrozenMap(effect.get("_linked_damage_source", {})),
                spawn_receipt=effect.get("spawn_settlement_receipt"),
                orders=orders,replacement_selections=selections,
            ),))
    except (TypeError,ValueError,KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def prepare_linked_settlement_order(host,continuation,intent,required,*,index,count):
    from .intent_replacement import serialized_replacement_selections

    group=required.orders.current_group
    if (index!=0 or count!=1 or not isinstance(intent,LinkedLandfallSettlementIntent) or intent.orders is not None
            or group is None or EXPLORE_SETTLE_FIELD not in continuation.effect):
        raise SemanticChoiceError("The linked settlement order lost its exact batch")
    return group.owner,_offer(continuation.effect,required.orders,serialized_replacement_selections(intent.replacement_selections))


def complete_linked_settlement_order(continuation,response,query):
    try:
        effect=dict(continuation.effect)
        orders,selections=_decode(effect)
        group=orders.current_group if orders is not None else None
        if (group is None or effect["player"]!=group.owner or group.owner not in query.active_seats
                or set(response)-{"cards","action","action_id","choice_schema"} or "cards" not in response
                or response.get("action","choose")!="choose"):
            raise ValueError("Only the current owner can order this batch")
        finished=orders.submit(group.owner,response["cards"])
        next_group=finished.current_group
        effect["player"]=next_group.owner if next_group else continuation.semantic_frame.controller
        effect[ORDER_FIELD]={"orders":finished.to_dict(),"selections":selections}
    except (TypeError,ValueError,KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(repeat_effect=FrozenMap(effect))


def commit_linked_settlement(host,intent):
    if intent.spawn_receipt is not None:
        from ..rules.impulse_spawn_settlement import commit_spawn_settlement

        return commit_spawn_settlement(host, intent)
    from ..zone_transitions import ZoneTransitionOwner
    from ..errors import GameRuleError

    snapshot=intent.damage_source["source_snapshot"]
    links=exploration_links(host,snapshot["object_id"],snapshot["logical_object_id"])
    if tuple(link.ref for link in links)!=intent.object_refs:
        raise GameRuleError("The original linked exile batch changed")
    if not links:
        return CommittedQuantityFollowup(())
    moved=ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((link.object_id,"graveyard") for link in links),reason=intent.reason,
        replacement_selections=tuple(thaw_value(v) for v in intent.replacement_selections),
        require_graveyard_order=True,graveyard_orders=intent.orders,
    )
    count=sum(card.zone=="graveyard" for card in moved)
    return CommittedQuantityFollowup((FrozenMap({"op":"damage_each_opponent","amount":count,
        **intent.damage_source}),) if count else ())
