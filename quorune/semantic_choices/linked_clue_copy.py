"""Offer a current public exiled model for the original source's Clue trigger."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..replacement.immutable import FrozenMap
from ..rules.linked_clue_copy import (
    LINKED_CLUE_COPY_PAIR_CONTEXT, LINKED_CLUE_COPY_VERSION, is_closed_linked_clue_copy_program,
    linked_clue_choice_effect, require_linked_clue_copy_version, validate_linked_clue_pair,
)
from ..rules.linked_exile_copy import linked_exile_copy_candidates
from ..rules.temporary_copy_subject import copy_subject_object_reference
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def linked_clue_copy_candidates(host, item, actor, effect):
    if item is None or item.kind != "triggered_ability" or item.controller != actor:
        raise SemanticChoiceError("Linked copy requires its original resolving trigger")
    program = host.semantics.get(item.semantic_key)
    if (program is None or program.event != "permanent.sacrificed"
            or not host.semantic_program_is_current_trusted(program)
            or not is_closed_linked_clue_copy_program(program)):
        raise SemanticChoiceError("Linked copy requires its trusted Clue sacrifice ability")
    try:
        source = ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
        pair = validate_linked_clue_pair(item.context.get(LINKED_CLUE_COPY_PAIR_CONTEXT))
        if pair != effect.get("ability_pair"):
            raise ValueError("Linked copy changed its captured ability pair")
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return tuple(sorted(row.ref for row in linked_exile_copy_candidates(
        host, source=source, ability_pair=pair,
    )))


def prepare_linked_clue_copy(effect, context):
    try:
        require_linked_clue_copy_version(effect)
        pair = validate_linked_clue_pair(effect.get("ability_pair"))
        expected = {**linked_clue_choice_effect(), "player": context.actor, "ability_pair": pair}
        if dict(effect) != expected or context.actor != context.stack_controller:
            raise ValueError("Linked copy requires its original resolving controller")
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    offered = {
        ref: copy_subject_object_reference(row)
        for ref in context.query.choice_candidate_refs()
        if (row := context.query.object(ref, zones=("exile",))) is not None
    }
    receiver = context.query.object(context.source_ref, zones=("battlefield",))
    if not offered or receiver is None:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="No current linked creature can supply the copy"),
        )
    saved = FrozenMap({**effect, "receiver": copy_subject_object_reference(receiver), "offered": offered})
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose a linked exiled creature card to copy, or decline.",
            choice=ObjectChoice("cards", tuple(offered), ("exile",), minimum=0, maximum=1, optional=True),
            public_context=FrozenMap({"operation": effect["op"], "stack": context.stack_ref}),
        ),
        continuation_effect=saved,
    )


def complete_linked_clue_copy(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    try:
        pair = validate_linked_clue_pair(effect.get("ability_pair"))
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    expected = {**linked_clue_choice_effect(), "player": actor, "ability_pair": pair}
    if (set(effect) != set(expected) | {"receiver", "offered"} or any(effect[key] != val for key, val in expected.items())
            or type(effect[LINKED_CLUE_COPY_VERSION]) is not int
            or not isinstance(effect["receiver"], Mapping) or not isinstance(effect["offered"], Mapping)):
        raise SemanticChoiceError("Linked copy lost its original choice evidence")
    if set(response) - {"cards", "action_id", "action", "choice_schema"} or "cards" not in response:
        raise SemanticChoiceError("Choose one offered exiled card or decline")
    refs = response["cards"]
    if not isinstance(refs, (list, tuple)) or len(refs) > 1 or any(type(ref) is not str for ref in refs):
        raise SemanticChoiceError("Choose at most one exact exiled reference")
    if not refs:
        return SemanticChoiceCompletion()
    ref, = refs
    row = query.object(ref, zones=("exile",))
    receiver = query.object(effect["receiver"].get("ref"), zones=("battlefield",))
    if (ref not in query.choice_candidate_refs() or row is None or receiver is None
            or effect["offered"].get(ref) != FrozenMap(copy_subject_object_reference(row))
            or effect["receiver"] != FrozenMap(copy_subject_object_reference(receiver))):
        raise SemanticChoiceError("The selected link or its source is no longer current")
    copied = FrozenMap({
        "op": "copy_until_end_of_turn", LINKED_CLUE_COPY_VERSION: 1, "source": receiver.ref,
        "subject": effect["offered"][ref], "ability_pair": pair,
    })
    return SemanticChoiceCompletion(prepend_effects=(copied,))
