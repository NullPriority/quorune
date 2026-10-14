from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from ..replacement.immutable import FrozenMap, thaw_value
from ..replacement_effects import (
    ReplacementChoiceRequired,
    ReplacementContinuation,
    ReplacementEffectError,
    replacement_choice_payload,
)
from ..semantic_runtime import IntentPlan, execute_intent_plan
from .defaults import default_semantic_choice_registry
from .intent_replacement import (
    semantic_intent_identity,
    serialized_replacement_selections,
    validate_semantic_intent_identity,
    with_replacement_selections,
)
from .model import (
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
)


_PILOT_ROLE = "pilot"


class SemanticPreparationCoordinationHost(Protocol):
    state: Any
    permissions: Any

    def _semantic_choice_context(
        self,
        item: Any,
        actor: str,
        effect: Mapping[str, Any],
    ) -> Any: ...

    def _validate_semantic_frame(
        self, frame: Mapping[str, Any], item: Any
    ) -> None: ...

    def _continue_resolution(
        self,
        *,
        stack_ref: str,
        effects: Sequence[Mapping[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int = 0,
    ) -> None: ...


def _issue_preparation_replacement_choice(
    host: SemanticPreparationCoordinationHost,
    *,
    continuation: SemanticChoiceContinuation,
    actor: str,
    intent: Any,
    intent_index: int,
    required: ReplacementChoiceRequired,
) -> None:
    intent_kind, identity = semantic_intent_identity(intent)
    pending = required.pending
    chooser = pending.choice.chooser
    host.permissions.issue(
        kind="replacement.order",
        role=_PILOT_ROLE,
        actors=[chooser],
        allowed_actions=["choose"],
        payload_by_actor={
            chooser: replacement_choice_payload(pending, required.effects)
        },
        continuation={
            "replacement_resume_kind": "semantic_preparation",
            "semantic_choice_continuation": continuation.to_dict(),
            "semantic_choice_actor": actor,
            "intent_index": intent_index,
            "semantic_intent_kind": intent_kind,
            "semantic_intent": identity,
            "replacement_selections": serialized_replacement_selections(
                intent.replacement_selections
            ),
            "replacement_batch": required.batch.to_dict(),
            "replacement_effects": [
                effect.to_dict() for effect in required.effects
            ],
        },
    )


def _finish_preparation(
    host: SemanticPreparationCoordinationHost,
    *,
    continuation: SemanticChoiceContinuation,
    actor: str,
    preparation: SemanticChoicePreparation,
    committed_results: Mapping[int,Any],
) -> None:
    if preparation.auto_continue is not None:
        host._continue_resolution(
            stack_ref=continuation.stack_ref,
            effects=[
                *(
                    thaw_value(value)
                    for value in preparation.auto_continue.resolved_effects(committed_results)
                ),
                *(thaw_value(value) for value in continuation.remaining),
            ],
            destination=continuation.destination,
            note=continuation.note,
            instruction_pointer=(
                continuation.semantic_frame.instruction_pointer + 1
            ),
        )
        return
    if preparation.request is None:
        raise SemanticChoiceError("Semantic preparation has no resume path")
    decision = host.permissions.issue(
        kind="semantic.choice",
        role=_PILOT_ROLE,
        actors=[actor],
        allowed_actions=["choose"],
        payload_by_actor={actor: preparation.request.payload()},
        continuation=continuation.to_dict(),
    )
    decision.continuation = continuation.with_pending_choice(
        decision.decision_id
    ).to_dict()


def continue_semantic_preparation(
    host: SemanticPreparationCoordinationHost,
    *,
    continuation: SemanticChoiceContinuation,
    actor: str,
    preparation: SemanticChoicePreparation,
    start_index: int = 0,
    replacement_selections: Sequence[
        str | FrozenMap | Mapping[str, Any]
    ] = (),
    expected_intent_kind: str | None = None,
    expected_intent: Mapping[str, Any] | None = None,
) -> bool:
    """Commit preparation intents, suspending before replaceable mutation."""

    intents = tuple(preparation.preparation_intents)
    if type(start_index) is not int or start_index < 0 or start_index > len(intents):
        raise SemanticChoiceError("Semantic preparation intent index is invalid")
    expected = None
    if expected_intent_kind is not None or expected_intent is not None:
        if expected_intent_kind is None or expected_intent is None:
            raise SemanticChoiceError(
                "Semantic preparation intent identity is incomplete"
            )
        expected = validate_semantic_intent_identity(
            expected_intent_kind, expected_intent
        )
        if start_index >= len(intents):
            raise SemanticChoiceError(
                "Semantic preparation intent disappeared before replacement resume"
            )
    committed_results={}
    for index in range(start_index, len(intents)):
        intent = intents[index]
        selections = replacement_selections if index == start_index else ()
        if selections or expected is not None:
            actual_kind, actual_identity = semantic_intent_identity(intent)
            if expected is not None and (
                actual_kind != expected_intent_kind or actual_identity != expected
            ):
                raise SemanticChoiceError(
                    "Semantic preparation intent changed before replacement resume"
                )
            intent = with_replacement_selections(intent, selections)
        try:
            from ..conditional_exile_return import defer_conditional_cost_returns
            with defer_conditional_cost_returns() as waiting:
                committed_results[index]=execute_intent_plan(host,IntentPlan(
                    operation=str(continuation.effect.get('op') or ''),handler_id=continuation.handler_id,intents=(intent,)))
            if waiting:
                from .completed_choice import encode_preparation_after_commit,finish_choice_intent_returns
                saved=encode_preparation_after_commit(continuation,actor,preparation,intents[index+1:],committed_results)
                if finish_choice_intent_returns(host,waiting,saved):return False
        except ReplacementChoiceRequired as required:
            semantic_intent_identity(intent)
            _issue_preparation_replacement_choice(
                host,
                continuation=continuation,
                actor=actor,
                intent=intent,
                intent_index=index,
                required=required,
            )
            return False
        expected = None
        replacement_selections = ()
    _finish_preparation(
        host,
        continuation=continuation,
        actor=actor,
        preparation=preparation,
        committed_results=committed_results,
    )
    return True


def resume_semantic_preparation(
    host: SemanticPreparationCoordinationHost,
    restored: ReplacementContinuation,
    selection: str | Mapping[str, Any],
    *,
    error_type: type[Exception],
) -> None:
    try:
        raw_continuation = restored.thaw_semantic_choice_continuation()
        expected_intent = restored.thaw_semantic_intent()
        registry = default_semantic_choice_registry()
        handler, continuation = registry.decode_continuation(raw_continuation)
        item = next(
            (
                candidate
                for candidate in host.state.stack
                if candidate.ref == continuation.stack_ref
            ),
            None,
        )
        if item is None:
            raise SemanticChoiceError(
                "Semantic preparation stack object no longer exists"
            )
        host._validate_semantic_frame(
            continuation.semantic_frame.to_dict(), item
        )
        actor = restored.semantic_choice_actor
        preparation = handler.prepare(
            continuation.effect,
            host._semantic_choice_context(item, actor, continuation.effect),
        )
        continue_semantic_preparation(
            host,
            continuation=continuation,
            actor=actor,
            preparation=preparation,
            start_index=restored.intent_index,
            replacement_selections=(
                *restored.replacement_selections,
                selection,
            ),
            expected_intent_kind=restored.semantic_intent_kind,
            expected_intent=expected_intent,
        )
    except (SemanticChoiceError, ReplacementEffectError) as exc:
        raise error_type(str(exc)) from exc
