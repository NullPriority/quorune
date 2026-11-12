from __future__ import annotations

"""Offer only the resolving controller's current dungeon destinations."""

from ..dungeon_catalog import DUNGEON_CATALOG
from ..dungeon_progress import (
    DungeonJourney, VentureOffer, VentureProgressIntent, prepare_venture, venture_instruction,
)
from ..initiative_model import TAKE_INITIATIVE_FIELD, TakeInitiativeIntent, initiative_instruction
from ..replacement.immutable import FrozenMap
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _journey(query) -> DungeonJourney:
    journey = query.venture_journey
    if not isinstance(journey, DungeonJourney):
        raise SemanticChoiceError("The resolving dungeon position is unavailable")
    return journey


def _option(room):
    definition = DUNGEON_CATALOG[room.dungeon]
    name = definition.source_name.split(" // ", 1)[0]
    return {"id": room.choice_id, "label": f"{name} — {definition.rooms[room.index].label}"}


def prepare_dungeon_choice(effect, context):
    if context.actor not in context.query.active_seats:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="The affected player has left the game."))
    if TAKE_INITIATIVE_FIELD in effect:
        try:
            initiative_instruction(effect, actor=context.actor)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            preparation_intents=(TakeInitiativeIntent(context.actor, context.stack_ref),),
            auto_continue=AutoContinue(reason="The affected player takes the initiative."))
    return prepare_venture_choice(effect, context)


def prepare_venture_choice(effect, context):
    actor = context.actor
    try:
        mode = venture_instruction(effect, actor=actor)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if not context.query.venture_allowed:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="Venture is prohibited for this player this turn."))
    journey = _journey(context.query)
    offer, retired = prepare_venture(actor, journey, mode)
    destinations = offer.destinations
    if len(destinations) == 1:
        intent = VentureProgressIntent(context.stack_ref, journey, offer, destinations[0])
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            preparation_intents=(intent,), auto_continue=AutoContinue(reason="Advance to the only legal room."))
    options = tuple(_option(room) for room in destinations)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose the next dungeon room.",
            choice=ScalarChoice(field_name="choice", legal_values=tuple(option["id"] for option in options)),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"], "options": options}),
        ),
        continuation_effect=FrozenMap({**effect, "_venture_offer": offer.to_dict()}),
        preparation_intents=((VentureProgressIntent(context.stack_ref, journey, offer, None),)
                             if retired is not None else ()),
    )


def complete_venture_choice(continuation, response, query):
    effect = dict(continuation.effect)
    try:
        offer = VentureOffer.from_dict(effect.pop("_venture_offer"))
        mode = venture_instruction(effect, actor=offer.owner)
    except (KeyError, ValueError, TypeError) as exc:
        raise SemanticChoiceError("The venture continuation is malformed") from exc
    journey = _journey(query)
    if offer.owner not in query.active_seats or not query.venture_allowed or mode != offer.mode or journey != offer.journey:
        raise SemanticChoiceError("The venture offer no longer belongs to this dungeon position")
    selected = next((room for room in offer.destinations if room.choice_id == response.get("choice")), None)
    if selected is None:
        raise SemanticChoiceError("Choose one of the offered dungeon rooms")
    intent = VentureProgressIntent(continuation.stack_ref, journey, offer, selected)
    return SemanticChoiceCompletion(intents=(intent,))
