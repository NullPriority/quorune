"""Choose one current card from the exact committed Mill batch."""

from ..replacement.immutable import FrozenMap
from ..rules.milled_permanent_battlefield import (
    eligible_milled_permanent, mill_battlefield_choice_candidates,
)
from .model import (
    ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _eligible(effect, *, actor, query):
    if actor not in query.active_seats:
        raise SemanticChoiceError("Committed Mill choice requires the active resolving controller")
    try:
        candidates = mill_battlefield_choice_candidates(effect, actor=actor)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    rows = []
    for candidate in candidates:
        row = query.object(candidate.ref, zones=(candidate.zone,))
        if (row is None or row.object_id != candidate.object_id or row.owner != actor
                or row.logical_object_id != candidate.logical_object_id or not row.known_to_actor
                or not row.physical_card or row.token or row.face_down
                or not eligible_milled_permanent(row.types)):
            raise SemanticChoiceError("A committed Mill choice lost its public eligible incarnation")
        rows.append((candidate, row))
    return tuple(rows)


def prepare_milled_permanent_battlefield(effect, context):
    actor = context.actor
    if actor != context.stack_controller:
        raise SemanticChoiceError("Committed Mill choice changed its resolving controller")
    rows = _eligible(effect, actor=actor, query=context.query)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose one milled creature, enchantment, or land card to put onto the battlefield.",
            choice=ObjectChoice(field_name="card", legal_refs=tuple(row.ref for _, row in rows),
                                zones=tuple(dict.fromkeys(row.zone for _, row in rows)),
                                minimum=1, maximum=1, optional=False, owner_relation="actor"),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "choose_option",
                                      "objects": tuple({"id": row.ref, "name": row.printed_name}
                                                       for _, row in rows)}),
        ),
        continuation_effect=FrozenMap(effect),
    )


def complete_milled_permanent_battlefield(continuation, response, query):
    actor = continuation.semantic_frame.controller
    rows = _eligible(continuation.effect, actor=actor, query=query)
    selected = response.get("card")
    choice = next((candidate for candidate, _ in rows if candidate.ref == selected), None)
    if type(selected) is not str or choice is None or selected not in query.choice_candidate_refs():
        raise SemanticChoiceError("Choose one of the eligible cards from the committed Mill")
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
        "op": "move_if_in_zone", "card": choice.ref, "from": choice.zone,
        "expected_zone_change_counter": choice.zone_change_counter, "destination": "battlefield",
        "controller": actor,
    }),))
