from __future__ import annotations

"""Choose zero, one, or two cards from the original public Mill receipt."""

from ..replacement.immutable import FrozenMap
from ..rules.milled_creature_land_to_hand import (
    MAXIMUM, MILL_HAND_MOVE, eligible_milled_hand_card, mill_hand_choice_candidates,
)
from .model import (
    ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


def _eligible(effect, *, actor, query):
    if actor not in query.active_seats:
        raise SemanticChoiceError("Committed Mill hand choice needs its active controller")
    try:
        candidates = mill_hand_choice_candidates(effect, actor=actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    rows = []
    for candidate in candidates:
        row = query.object(candidate.ref, zones=(candidate.zone,))
        if (row is None or row.object_id != candidate.object_id or row.owner != actor
                or row.logical_object_id != candidate.logical_object_id or not row.known_to_actor
                or not row.physical_card or row.token or row.face_down or not eligible_milled_hand_card(row.types)):
            raise SemanticChoiceError("Committed Mill hand choice lost a public eligible incarnation")
        rows.append((candidate, row))
    return tuple(rows)


def prepare_milled_hand_choice(effect, context):
    if context.actor != context.stack_controller:
        raise SemanticChoiceError("Committed Mill hand choice changed its resolving controller")
    rows = _eligible(effect, actor=context.actor, query=context.query)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Put up to two milled creature or land cards into your hand.",
            choice=ObjectChoice(
                field_name="cards", legal_refs=tuple(row.ref for _, row in rows),
                zones=tuple(dict.fromkeys(row.zone for _, row in rows)),
                minimum=0, maximum=min(MAXIMUM, len(rows)), optional=True, owner_relation="actor",
            ),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": "choose_option",
                "objects": tuple({"id": row.ref, "name": row.printed_name} for _, row in rows),
            }),
        ),
        continuation_effect=FrozenMap(effect),
    )


def complete_milled_hand_choice(continuation, response, query):
    actor = continuation.semantic_frame.controller
    rows = _eligible(continuation.effect, actor=actor, query=query)
    candidate_by_ref = {candidate.ref: candidate for candidate, _row in rows}
    selected = response.get("cards", ())
    offered = set(query.choice_candidate_refs())
    if (not isinstance(selected, (tuple, list)) or len(selected) > MAXIMUM
            or any(type(ref) is not str or ref not in candidate_by_ref or ref not in offered for ref in selected)
            or len(set(selected)) != len(selected)):
        raise SemanticChoiceError("Choose up to two distinct cards from the committed Mill")
    if not selected:
        return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
        "op": "move_if_in_zone", "player": actor,
        MILL_HAND_MOVE: {"schema_version": 1, "candidates": [candidate_by_ref[ref].to_dict() for ref in selected]},
    }),))
