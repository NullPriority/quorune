from __future__ import annotations

"""A nontarget public-exile choice with a sealed current object identity."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.void_counter_play import CHOICE_FIELD, grant_effect, is_choice_effect
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


CANDIDATES_FIELD = "_void_counter_play_candidates"
_FIELDS = {"op", "player", "source", CHOICE_FIELD}
_RESPONSE_METADATA = {"action", "action_id", "a", "cap", "reason", "plan", "next", "choice_schema"}


def _candidates(query):
    rows = tuple(query.object(ref, zones=("exile",)) for ref in query.choice_candidate_refs())
    if any(row is None or row.token or row.counters.get("void", 0) <= 0 for row in rows):
        raise SemanticChoiceError("Exile choice requires its current marked cards")
    try:
        return {
            row.ref: (row, LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref))
            for row in rows
        }
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _validate(effect, actor):
    source = effect.get("source")
    if type(source) is not str or not source or not is_choice_effect(effect, actor=actor, source=source):
        raise SemanticChoiceError("The void-counter choice requires its resolving ability's controller")
    return source


def prepare_void_counter_play(effect, context):
    actor = context.actor
    _validate(effect, actor)
    if actor != context.stack_controller:
        raise SemanticChoiceError("Only the resolving ability's controller chooses an exiled card")
    candidates = _candidates(context.query)
    if not candidates:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="No opposing-owned exiled card with a void counter"),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose an opposing-owned exiled card with a void counter.",
            choice=ObjectChoice(
                field_name="objects", legal_refs=tuple(candidates), zones=("exile",),
                visibility="actor_private",
            ),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": effect["op"],
                "objects": [{"id": row.ref, "name": row.printed_name} for row, _ in candidates.values()],
            }),
        ),
        continuation_effect=FrozenMap({
            **dict(effect),
            CANDIDATES_FIELD: tuple(identity.to_dict() for _, identity in candidates.values()),
        }),
    )


def complete_void_counter_play(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    if set(effect) != _FIELDS | {CANDIDATES_FIELD} or actor not in query.active_seats:
        raise SemanticChoiceError("The void-counter choice lost its resolving controller")
    source = _validate({key: effect[key] for key in _FIELDS}, actor)
    if not isinstance(response, Mapping) or set(response) - _RESPONSE_METADATA != {"objects"}:
        raise SemanticChoiceError("Submit exactly one offered exiled card")
    objects = response["objects"]
    if not isinstance(objects, (tuple, list)) or len(objects) != 1 or type(objects[0]) is not str:
        raise SemanticChoiceError("Submit exactly one offered exiled card")
    try:
        saved = tuple(LibrarySelectionObjectIdentity.from_dict(value) for value in effect[CANDIDATES_FIELD])
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    accepted = _candidates(query).get(objects[0])
    if accepted is None or accepted[1] not in saved:
        raise SemanticChoiceError("That exiled card or its offered incarnation is unavailable")
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(grant_effect(accepted[1], source=source)),))
