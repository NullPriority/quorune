from __future__ import annotations

"""Suspend and resume replacement-aware turn-based counter actions."""

from typing import Any, Mapping, Protocol, Sequence

from .counter_placement import validate_counter_event_subjects
from .errors import StateInvariantError
from .model import StackItem
from .replacement_effects import (
    ReplacementChoiceRequired,
    ReplacementContinuation,
    ReplacementEffectError,
    replacement_choice_payload,
)
from .saga_progression import SagaProgressionHost, saga_step_batch
from .trigger_processing import collect_trigger_items, enqueue_trigger_batch


_PILOT_ROLE = "pilot"
_SAGA_LORE_ACTION = "saga_lore"


class TurnCounterCoordinationHost(SagaProgressionHost, Protocol):
    permissions: Any

    def _grant_priority(self, seat: str | None) -> None: ...

    def _semantic_pause_annotation(self) -> Mapping[str, Any] | None: ...


def _turn_action_frame(host: TurnCounterCoordinationHost) -> dict[str, Any]:
    return {
        "active_player": host.state.active_player,
        "phase": host.state.phase,
        "step": host.state.step,
        "phase_index": host.state.phase_index,
        "turn_sequence": host.state.turn_sequence,
        "priority_player": host.state.priority_player,
        "stack_refs": [item.ref for item in host.state.stack],
    }


def _validate_turn_action_frame(
    host: TurnCounterCoordinationHost,
    frame: Mapping[str, Any],
) -> None:
    if dict(frame) != _turn_action_frame(host):
        raise ReplacementEffectError(
            "Turn-counter continuation state changed before resume"
        )


def _canonical_selections(
    values: Sequence[str | None | Mapping[str, Any]],
) -> list[str | dict[str, Any]]:
    result: list[str | dict[str, Any]] = []
    for value in values:
        if type(value) is str and value:
            result.append(value)
            continue
        if isinstance(value, Mapping) and value:
            result.append(dict(value))
            continue
        raise ReplacementEffectError(
            "Turn-counter replacement selections must be canonical values"
        )
    return result


def issue_turn_counter_replacement_choice(
    host: TurnCounterCoordinationHost,
    *,
    controller: str,
    held_triggers: Sequence[StackItem],
    selections: Sequence[str | None | Mapping[str, Any]],
    required: ReplacementChoiceRequired,
) -> None:
    """Suspend one CR 714.3c batch before any lore counter is placed."""

    if host.state.phase != "precombat_main" or host.state.step != "main":
        raise ReplacementEffectError(
            "Saga lore replacement ordering requires precombat main"
        )
    if controller != host.state.active_player:
        raise ReplacementEffectError(
            "Saga lore replacement ordering requires the active player"
        )
    if any(not isinstance(item, StackItem) for item in held_triggers):
        raise ReplacementEffectError(
            "Turn-counter held triggers must be typed stack items"
        )
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
            "replacement_resume_kind": "turn_counter_action",
            "turn_action_kind": _SAGA_LORE_ACTION,
            "turn_action_actor": controller,
            "turn_action_frame": _turn_action_frame(host),
            "held_triggers": [item.to_dict() for item in held_triggers],
            "replacement_selections": _canonical_selections(selections),
            "replacement_batch": required.batch.to_dict(),
            "replacement_effects": [
                effect.to_dict() for effect in required.effects
            ],
        },
    )


def coordinate_turn_counter_step(
    host: TurnCounterCoordinationHost,
    controller: str,
    phase: str,
    step: str,
    held_triggers: Sequence[StackItem],
    *,
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
    replacement_event_ids: Sequence[str] | None = None,
) -> tuple[StackItem, ...] | None:
    """Run represented turn-counter actions or pin their CR 616 choice."""

    try:
        waiting = saga_step_batch(
            host,
            controller,
            phase,
            step,
            held_triggers,
            replacement_selections=replacement_selections,
            replacement_event_ids=replacement_event_ids,
        )
    except ReplacementChoiceRequired as required:
        issue_turn_counter_replacement_choice(
            host,
            controller=controller,
            held_triggers=held_triggers,
            selections=replacement_selections,
            required=required,
        )
        return None
    return tuple(waiting)


def complete_ordinary_priority_step_entry(
    host: TurnCounterCoordinationHost,
    held_triggers: Sequence[StackItem],
    *,
    grant_priority: bool = True,
) -> bool:
    """Finish an ordinary step entry after its turn-based actions."""

    active = host.state.active_player
    if active is None:
        raise StateInvariantError("A turn has no active player")
    from .extra_combat_model import later_main_phase_context

    context = {
        "phase": host.state.phase,
        "step": host.state.step,
        "player": active,
        **later_main_phase_context(host.state, host.state.phase, host.state.step),
    }
    waiting = collect_trigger_items(
        host,
        "step.begin",
        context,
        held_triggers=held_triggers,
    )
    if host._semantic_pause_annotation() is not None:
        return False
    enqueue_trigger_batch(host, waiting)
    if grant_priority:
        host._grant_priority(active)
    return True


def resume_turn_counter_replacement(
    host: TurnCounterCoordinationHost,
    restored: ReplacementContinuation,
    selection: str | Mapping[str, Any],
    *,
    error_type: type[Exception],
) -> None:
    """Resume an immutable turn-counter batch and enter priority once."""

    try:
        if restored.turn_action_kind != _SAGA_LORE_ACTION:
            raise ReplacementEffectError(
                "Turn-counter continuation action is unsupported"
            )
        _validate_turn_action_frame(host, restored.thaw_turn_action_frame())
        validate_counter_event_subjects(host, restored.batch.events)
        held_triggers = tuple(
            StackItem.from_dict(value.to_dict())
            for value in restored.held_triggers
        )
        waiting = coordinate_turn_counter_step(
            host,
            restored.turn_action_actor,
            host.state.phase,
            host.state.step,
            held_triggers,
            replacement_selections=(
                *restored.replacement_selections,
                selection,
            ),
            replacement_event_ids=tuple(
                event.event_id for event in restored.batch.events
            ),
        )
    except (ReplacementEffectError, ValueError) as exc:
        raise error_type(str(exc)) from exc
    if waiting is not None:
        complete_ordinary_priority_step_entry(
            host,
            waiting,
        )


__all__ = [
    "complete_ordinary_priority_step_entry",
    "coordinate_turn_counter_step",
    "issue_turn_counter_replacement_choice",
    "resume_turn_counter_replacement",
]
