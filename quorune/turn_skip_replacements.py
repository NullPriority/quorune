from __future__ import annotations

"""Offer and resume CR 616 choices before an upcoming turn step begins."""

from typing import Any

from .errors import GameRuleError
from .model import StackItem
from .replacement.immutable import thaw_value
from .replacement_effects import (
    ReplaceableEvent,
    ReplacementClass,
    ReplacementEffect,
    ReplacementEventBatch,
    SetField,
    advance_replacement_batch,
    replacement_choice_payload,
)
from .semantic_runtime.untap_steps import active_step_skip_sources, current_step_is_skipped
from .turn_skip_model import TURN_BOUNDARY_SKIP_EVENT, TURN_BOUNDARY_SKIP_RESUME, TurnBoundary


_STEP_BOUNDARIES = {
    "beginning_combat": TurnBoundary.COMBAT,
    "untap": TurnBoundary.UNTAP,
    "upkeep": TurnBoundary.UPKEEP,
    "draw": TurnBoundary.DRAW,
}


def _frame(host: Any) -> dict[str, Any]:
    state = host.state
    return {
        "active_player": state.active_player,
        "phase": state.phase,
        "step": state.step,
        "phase_index": state.phase_index,
        "turn_sequence": state.turn_sequence,
        "priority_player": state.priority_player,
        "stack_refs": [item.ref for item in state.stack],
    }


def _replacement(effect_id: str, source: str, label: str) -> ReplacementEffect:
    return ReplacementEffect(
        effect_id=effect_id, source_id=source, event_kind=TURN_BOUNDARY_SKIP_EVENT,
        replacement_class=ReplacementClass.OTHER, conditions={"skipped": False},
        operations=(SetField("skipped", True),), label=label,
    )


def _plan(host: Any, active: str, step: str):
    boundary = _STEP_BOUNDARIES.get(step)
    if boundary is None or active not in host.active_seats:
        return None
    state = host.state
    grants = {
        grant.grant_id: grant for grant in state.scheduled_turn_skips
        if grant.applies(active, boundary, state.turn_sequence)
    }
    if not grants:
        return None
    effects = [
        _replacement(grant.grant_id, grant.grant_id, f"Scheduled {boundary.value} skip ({grant.spec.scope.value})")
        for grant in grants.values()
    ]
    effects.extend(
        _replacement(f"static-skip:{source.logical_object_id}:{key}:{index}", source.ref,
                     f"{source.ref} — static {boundary.value} skip")
        for source, key, index in active_step_skip_sources(host, active, step)
    )
    identity = f"turn-boundary:{state.turn_sequence}:{state.phase_index}"
    event = ReplaceableEvent(
        identity, TURN_BOUNDARY_SKIP_EVENT, active,
        {"skipped": False, "boundary": boundary.value},
    )
    return ReplacementEventBatch(identity, (event,), tuple(host.apnap_order())), tuple(effects), grants


def apply_step_skip_replacement(host, active, step, held_triggers, *, selection=None) -> bool | None:
    """Return whether the boundary was skipped, or suspend at an issued choice."""

    plan = _plan(host, active, step) if host.state.scheduled_turn_skips else None
    if plan is None:
        if selection is not None:
            raise GameRuleError("Scheduled skip choice is no longer applicable")
        return current_step_is_skipped(host, active, step)
    batch, effects, grants = plan
    progress = advance_replacement_batch(batch, effects, selections=() if selection is None else (selection,))
    if progress.pending is not None:
        chooser = progress.pending.choice.chooser
        host.permissions.issue(
            kind="replacement.order",
            role="pilot",
            actors=[chooser],
            allowed_actions=["choose"],
            payload_by_actor={chooser: replacement_choice_payload(progress.pending, effects)},
            continuation={
                "replacement_resume_kind": TURN_BOUNDARY_SKIP_RESUME,
                "turn_action_actor": active,
                "turn_action_frame": _frame(host),
                "held_triggers": [item.to_dict() for item in held_triggers],
                "replacement_batch": batch.to_dict(),
                "replacement_effects": [effect.to_dict() for effect in effects],
            },
        )
        return None
    event, = progress.batch.events
    effect_id, = event.applied_effects
    grant = grants.get(effect_id)
    if grant is not None:
        host.turn_steps.consume_skip(grant)
    boundary = event.payload["boundary"]
    host._log(
        active, "turn.skip.applied", f"{active} skipped {boundary}.",
        {"boundary": boundary, "effect": effect_id}, importance=2, changed_players=[active],
    )
    return True


def resume_turn_boundary_skip(host, restored, selection, *, error_type):
    if type(selection) is not str or thaw_value(restored.turn_action_frame) != _frame(host):
        raise error_type("Scheduled skip continuation is stale or malformed")
    from .turn_step_owner import TURN_STEPS

    index = host.state.phase_index
    if not 0 <= index < len(TURN_STEPS):
        raise error_type("Scheduled skip boundary is invalid")
    _phase, step = TURN_STEPS[index]
    plan = _plan(host, restored.turn_action_actor, step)
    if plan is None or plan[:2] != (restored.batch, restored.effects):
        raise error_type("Scheduled skip offers no longer match the live boundary")
    host.turn_steps.enter_step(
        held_triggers=tuple(StackItem.from_dict(thaw_value(item.payload)) for item in restored.held_triggers),
        boundary_replacement=selection,
    )
