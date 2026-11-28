"""Keep the private choice and the graveyard owner's order separate through replay."""

from collections.abc import Mapping
from dataclasses import replace

from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from ..private_opponent_library_cast_model import (
    DAMAGE_GRAVE_ORDER, DAMAGE_SELECTED, damage_library_cast_selection, validate_private_opponent_library_cast_selection,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..semantic_runtime.library_selection_intent import LibrarySelectionIntent
from .intent_replacement import serialized_replacement_selections
from .model import (
    AutoContinue, OrderingChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


def _intent(effect, stack_ref, actor, query=None):
    player = effect["player"]
    instruction = {key:effect[key] for key in damage_library_cast_selection()}
    validate_private_opponent_library_cast_selection(instruction, player=player)
    looked = tuple(LibrarySelectionObjectIdentity.from_dict(value) for value in effect["_looked_objects"])
    selected = effect[DAMAGE_SELECTED]
    refs = tuple(row.ref for row in looked)
    if (type(actor) is not str or actor != effect["_choice_actor"] or actor == player
            or type(selected) is not str or selected not in refs
            or query is not None and (player not in query.active_seats
                                       or refs != query.library_refs(player, top_first=True)[:3])):
        raise SemanticChoiceError("The private library selection lost its original actor, window, or object.")
    return LibrarySelectionIntent(
        actor=actor, player=player, source_stack_ref=stack_ref, reason=effect["_stack_label"],
        looked_are_public=False, selected_are_public=False, require_graveyard_order=True,
        arrangement=LibrarySelectionArrangement(
            looked=looked, selected_refs=(selected,), remainder_refs=tuple(ref for ref in refs if ref != selected),
            selected_destination=instruction["selected_destination"], remainder_destination="graveyard",
            remainder_order="chosen",
        ),
    )


def prepare_damage_library_cast_commit(effect, context):
    if DAMAGE_GRAVE_ORDER in effect or context.actor != context.stack_controller:
        raise SemanticChoiceError("Only the original controller can commit the private library selection.")
    return SemanticChoicePreparation(
        None, FrozenMap(effect),
        preparation_intents=(_intent(effect, context.stack_ref, context.actor, context.query),),
        auto_continue=AutoContinue(reason="Move the original private and graveyard selections."),
    )


def _ordering_choice(group):
    return OrderingChoice("cards", group.refs, visibility="actor_private",
                          schema_extras=FrozenMap({"order": "bottom_to_top"}))


def _validate_order(orders, intent):
    group = orders.current_group
    if (group is None or len(orders.groups) != 1 or orders.choices or group.owner != intent.player
            or set(group.refs) != set(intent.arrangement.remainder_refs)):
        raise SemanticChoiceError("A graveyard order can contain only the selected player's actual remainder.")
    return group


def prepare_damage_library_graveyard_order(host, continuation, intent, required, *, index, count):
    effect = continuation.effect
    if (index != 0 or count != 1 or DAMAGE_GRAVE_ORDER in effect
            or replace(intent, replacement_selections=()) != _intent(
                effect, continuation.stack_ref, continuation.semantic_frame.controller,
            )):
        raise SemanticChoiceError("Graveyard ordering requires the original private partition.")
    group = _validate_order(required.orders, intent)
    objects = []
    for ref, identity in group.members:
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.ref != ref or card.logical_object_id != identity.logical_object_id
                or card.owner != group.owner or card.zone != "library"):
            raise SemanticChoiceError("The actual graveyard arrival changed before its owner could order it.")
        objects.append({"id": ref, "name": card.printed_name})
    saved = FrozenMap({**effect, DAMAGE_GRAVE_ORDER: {
        "orders": required.orders.to_dict(),
        "replacement_selections": serialized_replacement_selections(intent.replacement_selections),
    }})
    request = SemanticChoiceRequest(
        prompt="Order the cards entering your graveyard from bottom to top.",
        choice=_ordering_choice(group),
        public_context=FrozenMap({"operation": effect["op"], "stack": continuation.stack_ref, "objects": objects}),
    )
    return group.owner, SemanticChoicePreparation(request=request, continuation_effect=saved)


def complete_damage_library_graveyard_order(continuation, response, query):
    effect = continuation.effect
    saved = effect[DAMAGE_GRAVE_ORDER]
    if not isinstance(saved, Mapping) or set(saved) != {"orders", "replacement_selections"}:
        raise SemanticChoiceError("The graveyard order lost its saved replacements.")
    try:
        intent = _intent(effect, continuation.stack_ref, continuation.semantic_frame.controller)
        if intent.player not in query.active_seats:
            raise ValueError("The original graveyard owner left before ordering")
        orders = GraveyardArrivalOrders.from_dict(saved["orders"])
        group = _validate_order(orders, intent)
        if (set(response) - {"cards", "action", "action_id", "choice_schema"} or "cards" not in response
                or "choice_schema" in response and FrozenMap(response["choice_schema"]) != FrozenMap(_ordering_choice(group).choice_schema())):
            raise ValueError("Submit only the graveyard order authorized for the owner.")
        order = orders.submit(group.owner, response["cards"])
        intent = replace(intent, graveyard_orders=order, replacement_selections=thaw_value(saved["replacement_selections"]))
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(intents=(intent,))
