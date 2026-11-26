"""The controller chooses an exact face-up linked creature as the ability resolves."""

from ..linked_exile_return_model import ExiledObjectIdentity
from ..rules.linked_loyalty_exile_model import (
    LINKED_LOYALTY_CHOICE_OFFERED, linked_loyalty_entry_instruction, linked_loyalty_exile_instruction,
)
from ..replacement.immutable import FrozenMap
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _current_offers(query):
    offers = {}
    for ref in query.choice_candidate_refs():
        row = query.object(ref, zones=("exile",))
        if row is None or row.face_down or not row.known_to_actor or ref in offers:
            raise SemanticChoiceError("Linked loyalty choice lost a public exile candidate")
        try:
            offers[ref] = ExiledObjectIdentity(row.object_id, row.ref, row.logical_object_id).to_dict()
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
    return FrozenMap(offers)


def prepare_linked_loyalty_exile(effect, context):
    if effect != linked_loyalty_exile_instruction("choose") or context.actor != context.stack_controller:
        raise SemanticChoiceError("The original ability's controller must choose its linked creature")
    offered = _current_offers(context.query)
    if not offered:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="No linked exiled creature matches the announced mana value."),
        )
    rows = [context.query.object(ref, zones=("exile",)) for ref in offered]
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose a linked exiled creature card to put onto the battlefield.",
            choice=ObjectChoice("card", tuple(offered), ("exile",)),
            public_context=FrozenMap({
                "stack": context.stack_ref,
                "operation": effect["op"],
                "objects": [{"id": row.ref, "name": row.printed_name} for row in rows],
            }),
        ),
        continuation_effect=FrozenMap({**effect, LINKED_LOYALTY_CHOICE_OFFERED: offered}),
    )


def complete_linked_loyalty_exile(continuation, response, query):
    effect = continuation.effect
    offered = effect.get(LINKED_LOYALTY_CHOICE_OFFERED)
    expected = linked_loyalty_exile_instruction("choose")
    if (
        set(effect) != set(expected) | {LINKED_LOYALTY_CHOICE_OFFERED}
        or any(effect[key] != value for key, value in expected.items())
        or not offered or offered != _current_offers(query)
    ):
        raise SemanticChoiceError("Linked loyalty choice no longer has its original exile options")
    selected = response.get("card")
    if type(selected) is not str or selected not in offered:
        raise SemanticChoiceError("Choose exactly one eligible creature card from linked exile")
    try:
        identity = ExiledObjectIdentity.from_dict(offered[selected])
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(linked_loyalty_entry_instruction(identity)),))
