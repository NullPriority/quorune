"""A committed, nontarget choice of an owned silver-counter exile card."""
from ..discover_model import discover_zone_counter
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.silver_exile_loyalty import (
    SILVER_EXILE_RETURN_CANDIDATES, is_silver_return, silver_return_effect,
)
from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _candidates(query, actor):
    return {
        row.ref: (row, LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref))
        for row in query.objects(zones=("exile",), owner=actor)
        if row.physical_card and not row.token and row.counters.get("silver", 0) > 0
    }


def prepare_silver_exile_return(effect, context):
    actor = context.actor
    if actor != context.stack_controller or not is_silver_return(effect, player=actor):
        raise SemanticChoiceError("The countered exile return belongs to its resolving controller")
    candidates = _candidates(context.query, actor)
    if not candidates:
        return SemanticChoicePreparation(None, FrozenMap(effect),
            auto_continue=AutoContinue(reason="No owned exile card has a silver counter."))
    return SemanticChoicePreparation(SemanticChoiceRequest(
        prompt="Choose a card you own in exile with a silver counter.",
        choice=ObjectChoice(field_name="objects", legal_refs=tuple(candidates),
                            zones=("exile",), owner_relation="you"),
        public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"],
            "objects": [{"id": row.ref, "name": row.printed_name if row.known_to_actor else "Exiled card"}
                        for row, _ in candidates.values()]})),
        FrozenMap({**effect, SILVER_EXILE_RETURN_CANDIDATES: tuple(
            identity.to_dict() for _, identity in candidates.values())}))


def complete_silver_exile_return(continuation, response, query):
    actor = continuation.semantic_frame.controller
    effect = continuation.effect
    if (actor not in query.active_seats or set(effect) != set(silver_return_effect(actor)) | {SILVER_EXILE_RETURN_CANDIDATES}
            or not is_silver_return({key: value for key, value in effect.items()
                                    if key != SILVER_EXILE_RETURN_CANDIDATES}, player=actor)):
        raise SemanticChoiceError("The countered exile return changed its resolving authority")
    choice = response.get("objects")
    if (set(response) - {"action", "action_id", "choice_schema"} != {"objects"}
            or not isinstance(choice, (tuple, list)) or len(choice) != 1 or type(choice[0]) is not str):
        raise SemanticChoiceError("Choose one offered, owned silver-counter exile card")
    try:
        saved = tuple(LibrarySelectionObjectIdentity.from_dict(raw)
                      for raw in effect[SILVER_EXILE_RETURN_CANDIDATES])
    except (ValueError, KeyError, TypeError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    current = _candidates(query, actor).get(choice[0])
    if current is None or current[1] not in saved:
        raise SemanticChoiceError("The selected card or its exact exile incarnation is unavailable")
    identity = current[1]
    return SemanticChoiceCompletion(intents=(ZoneMoveIntent(
        actor, identity.ref, ("exile",), "hand", "Return owned card with a silver counter",
        owned_only=True, expected_zone_change_counter=discover_zone_counter(identity)),))
