from __future__ import annotations

"""Legend keep collection before the existing simultaneous SBA execution."""

from dataclasses import replace
from contextlib import contextmanager
from contextvars import ContextVar

from .characteristic_read_frames import characteristic_read_method
from .errors import GameRuleError
from .replacement.immutable import FrozenMap
from .state_based_legend_model import (
    LEGEND_BATCH_OPERATION_ID, LegendCandidateSnapshot, LegendChoicePlan,
    LegendConflictSnapshot, completed_legend_choice_plan, legend_rule_choice_version,
)

_CURRENT_KEEPS: ContextVar[LegendChoicePlan | None] = ContextVar('state_based_legend_keeps', default=None)


def current_legend_choices() -> LegendChoicePlan | None:
    return _CURRENT_KEEPS.get()


@contextmanager
def state_based_legend_choices(host, plan):
    plan = completed_legend_choice_plan(plan, getattr(host.state, 'legend_rule_choice_version', None))
    if plan is not None:
        validate_legend_choice_plan(host, plan)
    token = _CURRENT_KEEPS.set(plan)
    try:
        yield
    finally:
        _CURRENT_KEEPS.reset(token)


@characteristic_read_method
def _current_groups(host) -> tuple[LegendConflictSnapshot, ...]:
    from .rules.legend_rule import current_legend_conflicts

    # An exemption can only remove candidates from a possible conflict.
    if not host._legend_groups():
        return ()
    return tuple(
        LegendConflictSnapshot(group.controller, group.name, tuple(
            LegendCandidateSnapshot(object_id, host.state.cards[object_id].logical_object_id,
                                    host.state.cards[object_id].ref)
            for object_id in group.object_ids))
        for group in current_legend_conflicts(host)
    )


def validate_legend_choice_plan(host, plan: LegendChoicePlan) -> None:
    from .state_based_replacement_coordination import state_based_replacement_frame

    if legend_rule_choice_version(getattr(host.state, 'legend_rule_choice_version', None)) != 1:
        raise GameRuleError('Current legend choices require the declared execution mode')
    if (plan.player_order != tuple(host.apnap_order())
            or plan.frame != FrozenMap(state_based_replacement_frame(host, include_legend_choices=False))
            or plan.groups != _current_groups(host)):
        raise GameRuleError('Legend choice inputs changed before state-based settlement')


def coordinate_state_based_legend_choices(host, batch):
    version = legend_rule_choice_version(getattr(host.state, 'legend_rule_choice_version', None))
    pending = completed_legend_choice_plan(current_legend_choices(), version)
    if version is None:
        return batch
    groups = _current_groups(host)
    if not groups:
        if pending is not None:
            raise GameRuleError('Saved legend choices no longer describe current conflicts')
        return batch
    if pending is None:
        decision = host.state.pending_decision
        selection = decision.continuation.get('selection', {}) if decision is not None else {}
        if selection.get('operation_id') == LEGEND_BATCH_OPERATION_ID:
            plan = LegendChoicePlan.from_dict(selection['payload']['plan'])
            validate_legend_choice_plan(host, plan)
            return None
        from .state_based_replacement_coordination import state_based_replacement_frame
        plan = LegendChoicePlan(FrozenMap(state_based_replacement_frame(
            host, include_legend_choices=False)), tuple(host.apnap_order()), groups)
        host._begin_state_based_legend_choice(plan)
        return None
    validate_legend_choice_plan(host, pending)
    kept = set(pending.kept)
    moving = tuple(candidate.object_id for group in pending.groups for candidate in group.candidates
                   if candidate.object_id not in kept)
    if set(moving).intersection(value.object_id for value in batch.saga_sacrifices):
        raise GameRuleError('Combined final-Saga and legend-rule departures remain unrepresented')
    direct = tuple(dict.fromkeys((*batch.put_in_graveyard, *moving)))
    return replace(batch, put_in_graveyard=direct,
                   destroy=tuple(object_id for object_id in batch.destroy if object_id not in direct),
                   detach=tuple(object_id for object_id in batch.detach if object_id not in direct))


def finish_state_based_legend_choices(host) -> None:
    plan = current_legend_choices()
    if plan is not None:
        host._finish_state_based_legend_choices(plan)
        _CURRENT_KEEPS.set(None)


def reconcile_legend_choices_after_departure(host) -> None:
    decision = host.state.pending_decision
    selection = decision.continuation.get('selection', {}) if decision is not None else {}
    raw = decision.continuation if decision is not None else {}
    frame = raw.get('state_based_frame', raw.get('composition_state_based_order', {}).get('state_based_frame', {}))
    if (current_legend_choices() is None and 'legend_choices' not in frame
            and selection.get('operation_id') != LEGEND_BATCH_OPERATION_ID):
        return
    _CURRENT_KEEPS.set(None)
    host._discard_state_based_legend_choices()
    if not host.state.game_over and not host._stabilize():
        host._grant_priority(host.state.priority_player or host.state.active_player)
