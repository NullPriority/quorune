from __future__ import annotations

"""Resume one public hand choice and the actual graveyard order before Treasure."""

from collections.abc import Mapping
from dataclasses import replace

from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..rules.revealed_legendary_graveyard_treasure import (
    LEGENDARY_QUERY, LOOK_COUNT, revealed_legendary_treasure_effect, validate_revealed_legendary_treasure_effect,
)
from ..semantic_runtime.library_selection_intent import LibrarySelectionIntent
from .intent_replacement import serialized_replacement_selections
from .model import (
    AutoContinue, OrderingChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


TREASURE_SELECTED = "_committed_graveyard_treasure_selected"
TREASURE_ORDER = "_committed_graveyard_treasure_order"
TREASURE_READY = "_committed_graveyard_treasure_ready"


def _original_intent(effect, *, stack_ref, controller, query=None):
    if effect.get("player") != controller or effect.get("_choice_actor") != controller:
        raise SemanticChoiceError("The public library choice changed its resolving controller")
    instruction = {key: effect[key] for key in revealed_legendary_treasure_effect()}
    validate_revealed_legendary_treasure_effect(instruction, player=controller)
    looked = tuple(LibrarySelectionObjectIdentity.from_dict(value) for value in effect["_looked_objects"])
    refs = tuple(row.ref for row in looked)
    raw_selected = effect[TREASURE_SELECTED]
    if not isinstance(raw_selected, (tuple, list)):
        raise SemanticChoiceError("The public library selection lost its submitted legendary refs")
    selected = tuple(raw_selected)
    groups = effect.get("_eligible_groups", ())
    eligible = set(groups[0]) if len(groups) == 1 else set()
    if len(looked) > LOOK_COUNT or any(ref not in eligible for ref in selected):
        raise SemanticChoiceError("The public library selection lost its legendary subset")
    if query is not None:
        from ..object_query import object_matches_query

        if (controller not in query.active_seats
                or refs != query.library_refs(controller, top_first=True)[:LOOK_COUNT]):
            raise SemanticChoiceError("The publicly revealed library prefix changed before commitment")
        for ref in selected:
            row = query.object(ref, zones=("library",))
            if row is None or row.owner != controller or not object_matches_query(row, LEGENDARY_QUERY):
                raise SemanticChoiceError("A selected revealed card lost its legendary quality")
    return LibrarySelectionIntent(
        actor=controller, player=controller, source_stack_ref=stack_ref, reason=effect["_stack_label"],
        looked_are_public=True, selected_are_public=False, require_graveyard_order=True,
        committed_graveyard_treasure=True,
        arrangement=LibrarySelectionArrangement(
            looked=looked, selected_refs=selected, remainder_refs=tuple(ref for ref in refs if ref not in selected),
            remainder_destination="graveyard", remainder_order="chosen",
        ),
    )


def prepare_legendary_treasure_commit(effect, context):
    if context.actor != context.stack_controller or TREASURE_ORDER in effect:
        raise SemanticChoiceError("Only the original controller can commit the revealed legendary selection")
    try:
        intent = _original_intent(
            effect, stack_ref=context.stack_ref, controller=context.stack_controller, query=context.query,
        )
        ready = effect.get(TREASURE_READY)
        if ready is not None:
            orders, selections = _decode_saved_order(ready)
            _order_group(orders, intent, complete=True)
            intent = replace(intent, graveyard_orders=orders, replacement_selections=selections)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoicePreparation(
        None, FrozenMap(effect), preparation_intents=(intent,),
        auto_continue=AutoContinue(
            reason="Create Treasure from the actual graveyard arrivals.", committed_effects_from_result=0,
        ),
    )


def _order_group(orders, intent, *, complete=False):
    if len(orders.groups) != 1 or (orders.current_group is None) != complete:
        raise ValueError("The public selection requires the revealed owner's actual graveyard order")
    group = orders.groups[0]
    looked = {row.ref: (row.object_id, row.logical_object_id) for row in intent.arrangement.looked}
    if (group.owner != intent.player or any(
            looked.get(ref) != (identity.object_id, identity.logical_object_id) for ref, identity in group.members)):
        raise ValueError("The graveyard order must contain exact cards from the public reveal")
    return group


def _ordering_choice(group):
    return OrderingChoice("cards", group.refs, visibility="public",
                          schema_extras=FrozenMap({"order": "bottom_to_top"}))


def _decode_saved_order(value):
    if not isinstance(value, Mapping) or set(value) != {"orders", "replacement_selections"}:
        raise ValueError("The public graveyard order lost its saved replacement record")
    selections = value["replacement_selections"]
    if not isinstance(selections, (tuple, list)):
        raise ValueError("The public graveyard order has malformed replacements")
    return GraveyardArrivalOrders.from_dict(value["orders"]), tuple(thaw_value(selections))


def prepare_legendary_treasure_graveyard_order(host, continuation, intent, required, *, index, count):
    effect = continuation.effect
    if (index != 0 or count != 1 or TREASURE_ORDER in effect or TREASURE_READY in effect
            or replace(intent, replacement_selections=()) != _original_intent(
                effect, stack_ref=continuation.stack_ref, controller=continuation.semantic_frame.controller,
            )):
        raise SemanticChoiceError("Graveyard order must follow the original public partition")
    group = _order_group(required.orders, intent)
    objects = []
    for ref, identity in group.members:
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.ref != ref or card.logical_object_id != identity.logical_object_id
                or card.owner != group.owner or card.zone != "library"):
            raise SemanticChoiceError("A publicly revealed card changed before its owner could order it")
        objects.append({"id": ref, "name": card.printed_name})
    saved = FrozenMap({**effect, TREASURE_ORDER: {
        "orders": required.orders.to_dict(),
        "replacement_selections": serialized_replacement_selections(intent.replacement_selections),
    }})
    request = SemanticChoiceRequest(
        prompt="Order the cards entering your graveyard from bottom to top.",
        choice=_ordering_choice(group),
        public_context=FrozenMap({"operation": effect["op"], "stack": continuation.stack_ref, "objects": objects}),
    )
    return group.owner, SemanticChoicePreparation(request=request, continuation_effect=saved)


def complete_legendary_treasure_graveyard_order(continuation, response, query):
    effect = continuation.effect
    try:
        intent = _original_intent(
            effect, stack_ref=continuation.stack_ref, controller=continuation.semantic_frame.controller, query=query,
        )
        orders, selections = _decode_saved_order(effect[TREASURE_ORDER])
        group = _order_group(orders, intent)
        if (set(response) - {"cards", "action", "action_id", "choice_schema"} or "cards" not in response
                or "choice_schema" in response and FrozenMap(response["choice_schema"]) != FrozenMap(_ordering_choice(group).choice_schema())):
            raise ValueError("Submit the ordered cards using the issued graveyard choice")
        orders = orders.submit(group.owner, response["cards"])
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(repeat_effect=FrozenMap({
        **{key: value for key, value in effect.items() if key != TREASURE_ORDER},
        TREASURE_READY: {"orders": orders.to_dict(), "replacement_selections": selections},
    }))
