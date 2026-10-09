from __future__ import annotations

import copy
from typing import Any, Mapping, Protocol, Sequence
from .counter_doubling import named_counter_input_snapshot

from .aura import AuraEntryChoiceRequired, issue_aura_entry_choice
from .counter_placement import (
    CounterPlacementError,
    validate_counter_event_subjects,
)
from .replacement_effects import (
    ReplacementChoiceRequired,
    ReplacementContinuation,
    ReplacementEffect,
    ReplacementEffectError,
    ReplacementEventBatch,
    next_batch_replacement_choice,
    replacement_choice_payload,
)
from .semantic_runtime import IntentPlan, execute_intent_plan
from .replacement.immutable import thaw_value
from .mana_payment_continuations import (
    resume_mana_choice_capable_priority_action,
)
from .land_entry_coordination import resume_land_entry_priority_action
from .semantic_choices.counter_coordination import (
    resume_semantic_counter_completion,
    resume_semantic_intent_completion,
)
from .semantic_choices.preparation_coordination import (
    resume_semantic_preparation,
)
from .entry_counter_coordination import (
    resume_resolving_entry_replacement,
)
from .turn_counter_coordination import resume_turn_counter_replacement


_PILOT_ROLE = "pilot"


class ReplacementDecisionHost(Protocol):
    state: Any
    permissions: Any

    def _semantic_frame(
        self, item: Any, *, instruction_pointer: int
    ) -> dict[str, Any]: ...

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
        entry_replacement_selections: Sequence[
            str | Mapping[str, Any]
        ] = (),
    ) -> None: ...

    def apply_effect(
        self,
        effect: Mapping[str, Any],
        *,
        actor: str,
        as_cost: bool = False,
    ) -> Any: ...

    def _apply_combat_assignments(
        self,
        assignments: Sequence[Mapping[str, Any]],
        *,
        replacement_selections: Sequence[
            str | None | Mapping[str, Any]
        ] = (),
        replacement_event_ids: Sequence[str] = (),
    ) -> bool: ...

    def _grant_priority(self, seat: str | None) -> None: ...

    def _semantic_pause_annotation(self) -> Mapping[str, Any] | None: ...

def issue_replacement_order_choice(
    host: ReplacementDecisionHost,
    *,
    item: Any,
    effect: Mapping[str, Any],
    remaining: Sequence[Mapping[str, Any]],
    destination: str | None,
    note: str,
    instruction_pointer: int,
    required: ReplacementChoiceRequired,
) -> None:
    """Suspend one semantic instruction at a seat-scoped CR 616 choice."""

    pending = required.pending
    seat = pending.choice.chooser
    context = replacement_choice_payload(pending, required.effects)
    sealed_effect = copy.deepcopy(dict(effect))
    named_inputs = named_counter_input_snapshot(host, effect, actor=item.controller)
    if named_inputs is not None:
        sealed_effect["_named_counter_input_snapshot"] = named_inputs
    decision = host.permissions.issue(
        kind="replacement.order",
        role=_PILOT_ROLE,
        actors=[seat],
        allowed_actions=["choose"],
        payload_by_actor={seat: context},
        continuation={
            "stack_ref": item.ref,
            "effect": sealed_effect,
            "remaining": [copy.deepcopy(dict(value)) for value in remaining],
            "destination": destination,
            "note": note,
            "instruction_pointer": instruction_pointer,
            "semantic_frame": host._semantic_frame(
                item,
                instruction_pointer=instruction_pointer,
            ),
            "replacement_batch": required.batch.to_dict(),
            "replacement_effects": [
                replacement.to_dict() for replacement in required.effects
            ],
        },
    )
    decision.continuation["semantic_frame"]["pending_choice_id"] = (
        decision.decision_id
    )


def issue_combat_damage_replacement_choice(
    host: ReplacementDecisionHost,
    *,
    assignments: Sequence[Mapping[str, Any]],
    selections: Sequence[str | None | Mapping[str, Any]],
    required: ReplacementChoiceRequired,
) -> None:
    """Suspend simultaneous combat damage before any damage mutation."""

    if any(
        not (
            (isinstance(value, str) and bool(value))
            or isinstance(value, Mapping)
        )
        for value in selections
    ):
        raise ReplacementEffectError(
            "Combat replacement selections must be canonical values"
        )

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
            "replacement_resume_kind": "combat_damage",
            "combat_assignments": [
                copy.deepcopy(dict(value)) for value in assignments
            ],
            "replacement_selections": list(selections),
            "replacement_batch": required.batch.to_dict(),
            "replacement_effects": [
                replacement.to_dict()
                for replacement in required.effects
            ],
        },
    )


def _mana_damage_event_id(batch: ReplacementEventBatch) -> str:
    origins: set[str] = set()
    for event in batch.events:
        if event.kind == "damage":
            origins.add(event.event_id)
        raw = event.payload.get("damage_event_ids")
        if isinstance(raw, (list, tuple)):
            origins.update(str(value) for value in raw if str(value))
    if len(origins) != 1:
        raise ReplacementEffectError(
            "Mana-payment replacement continuation lost its damage event"
        )
    return next(iter(origins))


def _priority_action_cost_event_id(batch: ReplacementEventBatch) -> str:
    if len(batch.events) != 1:
        raise ReplacementEffectError(
            "Priority-action cost continuation must identify one event"
        )
    event = batch.events[0]
    if event.kind not in {"counter.place", "zone.change"} or not event.event_id:
        raise ReplacementEffectError(
            "Priority-action cost continuation has an unsupported event"
        )
    return event.event_id


def _validate_mana_payment_frame(
    host: ReplacementDecisionHost,
    frame: Mapping[str, Any],
) -> None:
    current = {
        "active_player": host.state.active_player,
        "phase": host.state.phase,
        "step": host.state.step,
        "turn_sequence": host.state.turn_sequence,
        "priority_player": host.state.priority_player,
        "priority_epoch": host.state.priority_epoch,
        "stack_refs": [item.ref for item in host.state.stack],
    }
    if dict(frame) != current:
        raise ReplacementEffectError(
            "Mana-payment continuation state changed before resume"
        )


def apply_effect_with_replacement_choice(
    host: ReplacementDecisionHost,
    item: Any,
    effect: Mapping[str, Any],
    continuation: tuple[
        Sequence[Mapping[str, Any]], str | None, str, int
    ],
    *,
    plan: IntentPlan | None = None,
) -> bool:
    """Execute one legacy or typed effect, suspending at replacement choice."""

    remaining, destination, note, instruction_pointer = continuation
    try:
        if plan is None:
            host.apply_effect(effect, actor=item.controller, as_cost=False)
        else:
            execute_intent_plan(host, plan)
    except ReplacementChoiceRequired as required:
        issue_replacement_order_choice(
            host,
            item=item,
            effect=effect,
            remaining=remaining,
            destination=destination,
            note=note,
            instruction_pointer=instruction_pointer,
            required=required,
        )
        return False
    except AuraEntryChoiceRequired as required:
        issue_aura_entry_choice(
            host,
            item=item,
            effect=effect,
            remaining=remaining,
            destination=destination,
            note=note,
            instruction_pointer=instruction_pointer,
            required=required,
        )
        return False
    return (
        item in host.state.stack
        and host._semantic_pause_annotation() is None
    )


def _replacement_selection(
    response: Mapping[str, Any],
    pending: Any,
    *,
    error_type: type[Exception],
) -> str | Mapping[str, Any]:
    selected = response.get("replacement")
    if not isinstance(selected, str) or not selected:
        raise error_type("A replacement effect selection is required")
    if selected not in pending.choice.legal_selections:
        raise error_type("Selected replacement is not currently available")
    selected_event = response.get("replacement_event")
    if pending.event_order_options:
        if (
            not isinstance(selected_event, str)
            or selected_event not in pending.event_order_options
        ):
            raise error_type(
                "A currently available simultaneous event must be selected"
            )
    elif selected_event is not None:
        raise error_type(
            "This replacement choice does not accept an event selection"
        )
    allocation = response.get("prevention_allocation")
    allocation_choice = next(
        (
            value
            for value in pending.prevention_allocations
            if value.effect_id == selected
        ),
        None,
    )
    if allocation_choice is None:
        if allocation is not None:
            raise error_type(
                "This replacement does not accept a prevention allocation"
            )
        return (
            {"effect_id": selected, "event_id": selected_event}
            if selected_event is not None
            else selected
        )
    if allocation is None and allocation_choice.allocation_required:
        raise error_type(
            "The prevention amount must be divided among damage events"
        )
    if allocation is not None and not isinstance(allocation, Mapping):
        raise error_type("Prevention allocation must be an object")
    if allocation is not None:
        return {
            "effect_id": selected,
            "allocation": dict(allocation),
            **(
                {"event_id": selected_event}
                if selected_event is not None
                else {}
            ),
        }
    return (
        {"effect_id": selected, "event_id": selected_event}
        if selected_event is not None
        else selected
    )


def _resume_combat_replacement(
    host: ReplacementDecisionHost,
    restored: ReplacementContinuation,
    selection: str | Mapping[str, Any],
) -> None:
    waiting = host._apply_combat_assignments(
        restored.thaw_combat_assignments(),
        replacement_selections=[
            *restored.replacement_selections,
            selection,
        ],
        replacement_event_ids=[
            event.event_id
            for event in restored.batch.events
            if event.kind == "damage"
        ],
    )
    if not waiting:
        host._grant_priority(host.state.active_player)


def _resume_mana_replacement(
    host: ReplacementDecisionHost,
    restored: ReplacementContinuation,
    selection: str | Mapping[str, Any],
    *,
    error_type: type[Exception],
) -> None:
    try:
        _validate_mana_payment_frame(host, restored.thaw_priority_frame())
        response = restored.thaw_priority_response()
        event_id = (
            _mana_damage_event_id(restored.batch)
            if restored.resume_kind == "mana_payment"
            else _priority_action_cost_event_id(restored.batch)
            if len(restored.batch.events) == 1
            else _counted_activation_cost_event_id(restored)
        )
    except ReplacementEffectError as exc:
        raise error_type(str(exc)) from exc
    if restored.priority_action == 'activate' and len(restored.batch.events) > 1:
        pending = next_batch_replacement_choice(restored.batch, restored.effects)
        if pending is None or pending.event_order_options or type(selection) is not str:
            raise error_type('Counted activation replacement choice disappeared')
        event = restored.batch.events[pending.event_index]
        sequence = response.get('_counted_cost_replacement_sequence', [])
        if not isinstance(sequence, (list, tuple)):
            raise error_type('Counted activation replacement sequence is malformed')
        response['_counted_cost_replacement_sequence'] = [
            *sequence, {'object_ref': event.payload['object_ref'], 'selection': selection},
        ]
        resume_mana_choice_capable_priority_action(
            host, seat=restored.priority_seat, action=restored.priority_action, response=response,
        )
        return
    raw_journal = response.get("_mana_replacement_selections") or {}
    if not isinstance(raw_journal, Mapping):
        raise error_type("Mana-payment replacement journal is malformed")
    journal: dict[str, list[Any]] = {}
    for key, values in raw_journal.items():
        if not isinstance(key, str) or not isinstance(values, (list, tuple)):
            raise error_type("Mana-payment replacement journal is malformed")
        journal[key] = [thaw_value(value) for value in values]
    journal.setdefault(event_id, []).append(selection)
    response["_mana_replacement_selections"] = journal
    resume_mana_choice_capable_priority_action(
        host,
        seat=restored.priority_seat,
        action=restored.priority_action,
        response=response,
    )


def _counted_activation_cost_event_id(restored: ReplacementContinuation) -> str:
    """Resume only the current replacement within a decoded exact cost batch."""
    if restored.priority_action != 'activate' or any(
        event.kind != 'zone.change' for event in restored.batch.events
    ):
        raise ReplacementEffectError('Only counted activation costs have multiple cost events')
    pending = next_batch_replacement_choice(restored.batch, restored.effects)
    if pending is None or pending.choice.chooser != restored.priority_seat:
        raise ReplacementEffectError('Counted activation replacement choice disappeared')
    return pending.event_id


def _resume_land_entry_replacement(
    host: ReplacementDecisionHost,
    restored: ReplacementContinuation,
    selection: str | Mapping[str, Any],
    *,
    error_type: type[Exception],
) -> None:
    try:
        _validate_mana_payment_frame(host, restored.thaw_priority_frame())
        response = restored.thaw_priority_response()
    except ReplacementEffectError as exc:
        raise error_type(str(exc)) from exc
    resume_land_entry_priority_action(
        host,
        seat=restored.priority_seat,
        response=response,
        selections=[
            *(thaw_value(value) for value in restored.replacement_selections),
            selection,
        ],
    )


def _resume_semantic_replacement(
    host: ReplacementDecisionHost,
    restored: ReplacementContinuation,
    selection: str | Mapping[str, Any],
    *,
    error_type: type[Exception],
) -> None:
    stack_ref = restored.stack_ref
    item = next(
        (
            candidate
            for candidate in host.state.stack
            if candidate.ref == stack_ref
        ),
        None,
    )
    if item is None:
        raise error_type(
            "Replacement continuation stack object no longer exists"
        )
    host._validate_semantic_frame(restored.thaw_semantic_frame(), item)
    try:
        validate_counter_event_subjects(host, restored.batch.events)
    except CounterPlacementError as exc:
        raise error_type(str(exc)) from exc
    current_effect = restored.thaw_effect()
    snapshot=current_effect.pop('_named_counter_input_snapshot',None)
    current=named_counter_input_snapshot(host,current_effect,actor=item.controller)
    if current is not None and current!=snapshot:
        raise error_type('Named-counter quantity or membership changed before replacement resume')
    current_effect["_replacement_selections"] = [
        *list(current_effect.get("_replacement_selections") or []),
        selection,
    ]
    damage_event_ids = [
        event.event_id
        for event in restored.batch.events
        if event.kind == "damage"
    ]
    if damage_event_ids:
        current_effect["_replacement_event_ids"] = damage_event_ids
    host._continue_resolution(
        stack_ref=stack_ref,
        effects=[current_effect, *restored.thaw_remaining()],
        destination=restored.destination,
        note=restored.note,
        instruction_pointer=restored.instruction_pointer,
    )


def complete_replacement_order_choice(
    host: ReplacementDecisionHost,
    decision: Any,
    *,
    error_type: type[Exception],
) -> None:
    """Validate and append one exact replacement choice before resuming."""

    seat = decision.actors[0]
    response = decision.responses[seat]
    continuation = decision.continuation
    try:
        restored = ReplacementContinuation.from_dict(continuation)
    except ReplacementEffectError as exc:
        raise error_type(str(exc)) from exc
    batch = restored.batch
    effects = restored.effects
    pending = next_batch_replacement_choice(batch, effects)
    if pending is None or pending.choice.chooser != seat:
        raise error_type(
            "Replacement continuation no longer requires this chooser"
        )
    selection = _replacement_selection(
        response, pending, error_type=error_type
    )
    if restored.resume_kind == "combat_damage":
        _resume_combat_replacement(host, restored, selection)
        return
    if restored.resume_kind in {"mana_payment", "priority_action_cost"}:
        _resume_mana_replacement(
            host, restored, selection, error_type=error_type
        )
        return
    if restored.resume_kind == "land_entry":
        _resume_land_entry_replacement(
            host, restored, selection, error_type=error_type
        )
        return
    if restored.resume_kind == "semantic_counter_completion":
        resume_semantic_counter_completion(
            host,
            restored,
            selection,
            error_type=error_type,
        )
        return
    if restored.resume_kind == "semantic_intent_completion":
        resume_semantic_intent_completion(
            host,
            restored,
            selection,
            error_type=error_type,
        )
        return
    if restored.resume_kind == "semantic_preparation":
        resume_semantic_preparation(
            host,
            restored,
            selection,
            error_type=error_type,
        )
        return
    if restored.resume_kind == "resolving_entry":
        resume_resolving_entry_replacement(
            host,
            restored,
            selection,
            error_type=error_type,
        )
        return
    if restored.resume_kind == "turn_counter_action":
        resume_turn_counter_replacement(
            host,
            restored,
            selection,
            error_type=error_type,
        )
        return
    _resume_semantic_replacement(
        host, restored, selection, error_type=error_type
    )
