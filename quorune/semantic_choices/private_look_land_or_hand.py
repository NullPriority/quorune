"""Owner-private land selection or fallback hand choice over one committed look."""

from ..object_predicate import ObjectQuerySpec
from ..object_query import object_matches_query
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..rules.private_look_land_or_hand import private_land_hand_continuation, validate_private_land_hand_start
from ..semantic_runtime.intents import LibrarySelectionIntent, RevealLibraryCardsIntent
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_LAND_QUERY = ObjectQuerySpec(zones=("library",), types_all=("land",))


def _current_look(query, actor, count):
    refs = query.library_refs(actor, top_first=True)[:count]
    rows = tuple(query.object(ref, zones=("library",)) for ref in refs)
    if any(row is None or row.owner != actor for row in rows):
        raise SemanticChoiceError("The original owner-private library cards are unavailable")
    identities = tuple(LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref) for row in rows)
    return rows, identities


def _legal_land_refs(rows):
    return tuple(row.ref for row in rows if object_matches_query(row, _LAND_QUERY))


def _request(effect, context, rows, legal, *, reveal=()):
    land = effect["_land_hand_stage"] == "land"
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=("You may put a land onto the battlefield tapped." if land else "Choose a card to put into your hand."),
            choice=ObjectChoice("cards", legal, zones=("library",), minimum=0 if land else 1,
                                maximum=1, visibility="actor_private"),
            public_context=FrozenMap({
                "operation": effect["op"], "stack": context.stack_ref,
                "objects": [{"id": row.ref, "name": row.printed_name} for row in rows],
            }),
        ),
        continuation_effect=FrozenMap(effect), preparation_intents=reveal,
    )


def prepare_private_land_hand(effect, context):
    actor = context.actor
    if actor != context.stack_controller:
        raise SemanticChoiceError("The private look belongs to the resolving controller")
    try:
        if "_land_hand_stage" in effect:
            count, stage, expected = private_land_hand_continuation(effect, player=actor, stack_ref=context.stack_ref)
            rows, current = _current_look(context.query, actor, count)
            if current != expected or stage != "hand":
                raise SemanticChoiceError("The private hand fallback changed its original top cards")
            return _request(effect, context, rows, tuple(row.ref for row in rows))
        count = validate_private_land_hand_start(effect, player=actor)
        rows, seen = _current_look(context.query, actor, count)
        if not rows:
            return SemanticChoicePreparation(None, FrozenMap(effect),
                auto_continue=AutoContinue("There are no available library cards"))
        lands = _legal_land_refs(rows)
        saved = {**effect, "_land_hand_stage": "land" if lands else "hand",
                 "_land_hand_stack": context.stack_ref, "_land_hand_looked": [row.to_dict() for row in seen]}
        reveal = (RevealLibraryCardsIntent(actor, actor, actor, tuple(row.ref for row in rows),
                                          context.stack_label, public=False),)
        return _request(saved, context, rows, lands or tuple(row.ref for row in rows), reveal=reveal)
    except (TypeError, KeyError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def complete_private_land_hand(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    try:
        count, stage, expected = private_land_hand_continuation(effect, player=actor, stack_ref=continuation.stack_ref)
        rows, current = _current_look(query, actor, count)
        if current != expected:
            raise ValueError("The choice must use the original current library incarnations")
        selected = response.get("cards")
        land = stage == "land"
        legal = _legal_land_refs(rows) if land else tuple(row.ref for row in rows)
        if (set(response) - {"cards", "action", "action_id", "choice_schema"}
                or response.get("action", "choose") != "choose" or not isinstance(selected, (list, tuple))
                or not (0 if land else 1) <= len(selected) <= 1
                or any(type(ref) is not str or ref not in legal for ref in selected)):
            raise ValueError("Choose only an eligible card from the original private look")
        if not selected:
            return SemanticChoiceCompletion(repeat_effect=FrozenMap({**effect, "_land_hand_stage": "hand"}))
        remainder = tuple(row.ref for row in rows if row.ref not in selected)
        arrangement = LibrarySelectionArrangement(
            looked=current, selected_refs=selected, remainder_refs=remainder,
            remainder_destination="library_bottom", remainder_order="random",
            selected_destination="battlefield" if land else "hand", entry_tapped=land,
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(intents=(LibrarySelectionIntent(
        actor, actor, arrangement, continuation.note or "Private library selection", continuation.stack_ref,
        looked_are_public=False, selected_are_public=False,
    ),))
