from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from ..replacement.immutable import FrozenMap, thaw_value
from ..replacement_effects import (
    ReplacementChoiceRequired,
    ReplacementContinuation,
    ReplacementEffectError,
    replacement_choice_payload,
)
from ..counter_placement import (
    CounterPlacementError,
    validate_counter_event_subjects,
)
from ..semantic_runtime import (
    IntentPlan,
    LifeChangeIntent,
    LibrarySelectionIntent,
    MoveObjectsSimultaneouslyIntent,
    PlaceCounterBatchIntent,
    PlaceCountersIntent,
    PlaceCountersOnSetIntent,
    PlaceCountersOnTargetsIntent,
    PlacePlayerCountersIntent,
    ProliferateIntent,
    SurveilLibraryIntent,
    ZoneMoveIntent,
    execute_intent_plan,
)
from .defaults import default_semantic_choice_registry
from .intent_replacement import (
    counter_intent_identity,
    semantic_intent_identity,
    serialized_replacement_selections,
    validate_counter_intent_identity,
    validate_semantic_intent_identity,
    with_replacement_selections,
)
from .model import (
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
)


_PILOT_ROLE = "pilot"


class SemanticCounterCoordinationHost(Protocol):
    state: Any
    permissions: Any

    def _validate_semantic_frame(
        self, frame: Mapping[str, Any], item: Any
    ) -> None: ...

    def _semantic_choice_query(
        self,
        actor: str,
        *,
        response: Mapping[str, Any] | None = None,
        effect: Mapping[str, Any] | None = None,
        source_ref: str | None = None,
    ) -> Any: ...

    def _continue_resolution(
        self,
        *,
        stack_ref: str,
        effects: Sequence[Mapping[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int = 0,
    ) -> None: ...


def _serialized_selections(
    selections: Sequence[str | FrozenMap | Mapping[str, Any]],
) -> list[str | dict[str, Any]]:
    return [
        value if isinstance(value, str) else thaw_value(value)
        for value in selections
    ]


def _issue_counter_replacement_choice(
    host: SemanticCounterCoordinationHost,
    *,
    continuation: SemanticChoiceContinuation,
    actor: str,
    response: Mapping[str, Any],
    intent: PlaceCountersIntent,
    intent_index: int,
    selections: Sequence[str | FrozenMap | Mapping[str, Any]],
    required: ReplacementChoiceRequired,
) -> None:
    pending = required.pending
    seat = pending.choice.chooser
    context = replacement_choice_payload(pending, required.effects)
    host.permissions.issue(
        kind="replacement.order",
        role=_PILOT_ROLE,
        actors=[seat],
        allowed_actions=["choose"],
        payload_by_actor={seat: context},
        continuation={
            "replacement_resume_kind": "semantic_counter_completion",
            "semantic_choice_continuation": continuation.to_dict(),
            "semantic_choice_actor": actor,
            "semantic_choice_response": dict(response),
            "intent_index": intent_index,
            "counter_intent": counter_intent_identity(intent),
            "replacement_selections": _serialized_selections(selections),
            "replacement_batch": required.batch.to_dict(),
            "replacement_effects": [
                effect.to_dict() for effect in required.effects
            ],
        },
    )


def _issue_semantic_intent_replacement_choice(
    host: SemanticCounterCoordinationHost,
    *,
    continuation: SemanticChoiceContinuation,
    actor: str,
    response: Mapping[str, Any],
    intent: (
        PlaceCountersOnSetIntent
        | LifeChangeIntent
        | PlaceCounterBatchIntent
        | PlaceCountersOnTargetsIntent
        | PlacePlayerCountersIntent
        | ProliferateIntent
        | MoveObjectsSimultaneouslyIntent
        | ZoneMoveIntent
    ),
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
            "replacement_resume_kind": "semantic_intent_completion",
            "semantic_choice_continuation": continuation.to_dict(),
            "semantic_choice_actor": actor,
            "semantic_choice_response": dict(response),
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


def _source_ref(host: SemanticCounterCoordinationHost, item: Any) -> str | None:
    object_id = item.source_object_id or item.card_object_id or ""
    source = host.state.cards.get(object_id)
    return source.ref if source is not None else None


def continue_semantic_completion(
    host: SemanticCounterCoordinationHost,
    *,
    item: Any,
    continuation: SemanticChoiceContinuation,
    actor: str,
    response: Mapping[str, Any],
    completion: SemanticChoiceCompletion,
    start_index: int = 0,
    replacement_selections: Sequence[
        str | FrozenMap | Mapping[str, Any]
    ] = (),
    expected_counter_intent: Mapping[str, Any] | None = None,
    expected_intent_kind: str | None = None,
    expected_intent: Mapping[str, Any] | None = None,
) -> bool:
    """Execute a completion, suspending supported intents before mutation."""

    intents = tuple(completion.intents)
    if type(start_index) is not int or start_index < 0 or start_index > len(intents):
        raise SemanticChoiceError("Semantic completion intent index is invalid")
    if expected_counter_intent is not None and (
        expected_intent_kind is not None or expected_intent is not None
    ):
        raise SemanticChoiceError("Semantic completion has competing identities")
    if expected_counter_intent is not None:
        expected_kind = "place_counters"
        expected = validate_counter_intent_identity(expected_counter_intent)
    elif expected_intent_kind is not None or expected_intent is not None:
        if expected_intent_kind is None or expected_intent is None:
            raise SemanticChoiceError(
                "Semantic completion intent identity is incomplete"
            )
        expected_kind = expected_intent_kind
        expected = validate_semantic_intent_identity(
            expected_intent_kind, expected_intent
        )
    else:
        expected_kind = None
        expected = None
    for index in range(start_index, len(intents)):
        intent = intents[index]
        selections = replacement_selections if index == start_index else ()
        if selections or expected is not None:
            actual_kind, identity = semantic_intent_identity(intent)
            if expected is not None and (
                actual_kind != expected_kind or identity != expected
            ):
                raise SemanticChoiceError(
                    "Semantic completion intent changed before replacement resume"
                )
            intent = with_replacement_selections(intent, selections)
        try:
            execute_intent_plan(
                host,
                IntentPlan(
                    operation=str(continuation.effect.get("op") or ""),
                    handler_id=continuation.handler_id,
                    intents=(intent,),
                ),
            )
        except ReplacementChoiceRequired as required:
            if isinstance(intent, PlaceCountersIntent):
                _issue_counter_replacement_choice(
                    host,
                    continuation=continuation,
                    actor=actor,
                    response=response,
                    intent=intent,
                    intent_index=index,
                    selections=intent.replacement_selections,
                    required=required,
                )
            elif isinstance(
                intent,
                (
                    PlaceCountersOnSetIntent,
                    LifeChangeIntent,
                    PlaceCounterBatchIntent,
                    PlaceCountersOnTargetsIntent,
                    PlacePlayerCountersIntent,
                    ProliferateIntent,
                    MoveObjectsSimultaneouslyIntent,
                    SurveilLibraryIntent,
                    LibrarySelectionIntent,
                    ZoneMoveIntent,
                ),
            ):
                _issue_semantic_intent_replacement_choice(
                    host,
                    continuation=continuation,
                    actor=actor,
                    response=response,
                    intent=intent,
                    intent_index=index,
                    required=required,
                )
            else:
                raise
            return False
        expected = None
        replacement_selections = ()
    if item not in host.state.stack:
        return True
    remaining = [
        *(thaw_value(value) for value in completion.prepend_effects),
        *(thaw_value(value) for value in continuation.remaining),
    ]
    if completion.repeat_effect is not None:
        remaining.insert(0, thaw_value(completion.repeat_effect))
    host._continue_resolution(
        stack_ref=continuation.stack_ref,
        effects=remaining,
        destination=continuation.destination,
        note=continuation.note,
        instruction_pointer=(
            continuation.semantic_frame.instruction_pointer + 1
        ),
    )
    return True


def resume_semantic_counter_completion(
    host: SemanticCounterCoordinationHost,
    restored: ReplacementContinuation,
    selection: str | Mapping[str, Any],
    *,
    error_type: type[Exception],
) -> None:
    try:
        raw_continuation = restored.thaw_semantic_choice_continuation()
        response = restored.thaw_semantic_choice_response()
        expected_intent = validate_counter_intent_identity(
            restored.thaw_counter_intent()
        )
        validate_counter_event_subjects(host, restored.batch.events)
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
                "Semantic counter continuation stack object no longer exists"
            )
        host._validate_semantic_frame(
            continuation.semantic_frame.to_dict(), item
        )
        actor = restored.semantic_choice_actor
        completion = handler.complete(
            continuation,
            response,
            host._semantic_choice_query(
                actor,
                response=response,
                effect=continuation.effect,
                source_ref=_source_ref(host, item),
            ),
        )
        continue_semantic_completion(
            host,
            item=item,
            continuation=continuation,
            actor=actor,
            response=response,
            completion=completion,
            start_index=restored.intent_index,
            replacement_selections=(
                *restored.replacement_selections,
                selection,
            ),
            expected_counter_intent=expected_intent,
        )
    except (
        CounterPlacementError,
        SemanticChoiceError,
        ReplacementEffectError,
    ) as exc:
        raise error_type(str(exc)) from exc


def resume_semantic_intent_completion(
    host: SemanticCounterCoordinationHost,
    restored: ReplacementContinuation,
    selection: str | Mapping[str, Any],
    *,
    error_type: type[Exception],
) -> None:
    try:
        raw_continuation = restored.thaw_semantic_choice_continuation()
        response = restored.thaw_semantic_choice_response()
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
                "Semantic intent continuation stack object no longer exists"
            )
        host._validate_semantic_frame(
            continuation.semantic_frame.to_dict(), item
        )
        actor = restored.semantic_choice_actor
        completion = handler.complete(
            continuation,
            response,
            host._semantic_choice_query(
                actor,
                response=response,
                effect=continuation.effect,
                source_ref=_source_ref(host, item),
            ),
        )
        continue_semantic_completion(
            host,
            item=item,
            continuation=continuation,
            actor=actor,
            response=response,
            completion=completion,
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
