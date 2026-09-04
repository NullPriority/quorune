from __future__ import annotations

"""Authoritative priority, yield, and decision-window state transitions."""

import copy
import hashlib
from typing import Any, Mapping

from .errors import GameRuleError, StateInvariantError
from .mana_payment_continuations import (
    execute_mana_choice_capable_priority_action,
)
from .land_entry_coordination import execute_land_entry_priority_action
from .mana_undo import (
    ManaUndoError,
    clear_mana_undo_stack,
    undo_mana_activation,
)
from .model import DecisionGroup, GameState, YieldPolicy
from .rules.action_catalog import action_offer_signature_facts
from .trigger_processing import start_delayed_trigger_batch
from .turn_priority_model import (
    PriorityGrantPlan,
    PriorityPassPlan,
    TurnPriorityHost,
    YieldInvalidationReason,
)
from .util import stable_json


_YIELD_MODES = (
    "until_public_change",
    "until_my_turn",
    "auto_if_no_response",
)


class TurnPriorityDecisionOwner:
    """Single mutation owner for represented priority and yield progression."""

    def __init__(self, host: TurnPriorityHost) -> None:
        self.host = host

    @property
    def state(self) -> GameState:
        return self.host.state

    def yield_change_epoch(self, kind: str, seat: str | None = None) -> int:
        key = (
            f"yield_change:{kind}:{seat}"
            if seat is not None
            else f"yield_change:{kind}"
        )
        return int(self.state.ref_counters.get(key, 0))

    def increment_yield_change_epoch(
        self, kind: str, seat: str | None = None
    ) -> int:
        key = (
            f"yield_change:{kind}:{seat}"
            if seat is not None
            else f"yield_change:{kind}"
        )
        next_value = int(self.state.ref_counters.get(key, 0)) + 1
        self.state.ref_counters[key] = next_value
        return next_value

    def update_yield_change_epochs(self, event: Any) -> None:
        if event.code in {
            "stack.cast",
            "stack.activate",
            "stack.trigger",
            "stack.resolve",
            "stack.counter",
        }:
            self.increment_yield_change_epoch("stack")
            return
        if event.code == "card.draw.private":
            for seat in self.state.players:
                if seat in event.visibility:
                    self.increment_yield_change_epoch("draw", seat)
            return
        if event.code == "zone.move":
            if (
                event.details.get("from") == "hand"
                or event.details.get("to") == "hand"
            ):
                for seat in event.changed_players:
                    if seat in self.state.players:
                        self.increment_yield_change_epoch("action", seat)
            self.increment_yield_change_epoch("public")
            return
        if event.code == "permanent.untap":
            for seat in event.changed_players:
                if seat in self.state.players:
                    self.increment_yield_change_epoch("action", seat)
            self.increment_yield_change_epoch("public")
            return
        if event.code in {
            "land.play",
            "monarch.change",
            "game.day_night",
            "permanent.transform",
            "token.create",
            "control.change",
            "permanent.goad",
            "permanent.goad.expire",
            "player.eliminated",
        }:
            self.increment_yield_change_epoch("public")

    def prepare_priority_grant(
        self,
        seat: str | None,
        *,
        cleanup_frame: bool,
    ) -> PriorityGrantPlan | None:
        active = self.host.active_seats
        if not active:
            return None
        resolved = seat
        if resolved not in active:
            anchor = resolved or self.state.active_player or active[0]
            resolved = self.host._next_active_after(anchor)
        return PriorityGrantPlan(
            seat=resolved,
            priority_epoch=self.state.priority_epoch + 1,
            cleanup_frame=cleanup_frame,
        )

    def commit_priority_grant(self, plan: PriorityGrantPlan) -> None:
        if plan.seat not in self.host.active_seats:
            raise StateInvariantError("Priority grant seat is no longer active")
        if plan.priority_epoch != self.state.priority_epoch + 1:
            raise StateInvariantError("Priority grant plan is stale")
        self.state.priority_player = plan.seat
        self.state.priority_passes = []
        self.state.priority_epoch = plan.priority_epoch
        if plan.cleanup_frame:
            cleanup_frame = self.host._active_cleanup_frame()
            if cleanup_frame is None:
                raise StateInvariantError("Cleanup priority frame disappeared")
            cleanup_frame["priority_granted"] = True

    def grant_priority(self, seat: str | None) -> None:
        if self.host._stabilize():
            return
        if not self.host.active_seats:
            return
        cleanup_frame = self.host._active_cleanup_frame()
        if (
            cleanup_frame is not None
            and not cleanup_frame.get("delayed_triggers_queued", False)
        ):
            cleanup_frame["delayed_triggers_queued"] = True
            delayed_ids = {
                str(value)
                for value in cleanup_frame.get("delayed_trigger_ids", [])
            }
            delayed = [
                trigger
                for trigger in self.state.delayed_triggers
                if trigger.trigger_id in delayed_ids
            ]
            if delayed:
                start_delayed_trigger_batch(
                    self.host,
                    delayed,
                    after="grant_priority",
                )
                return
        plan = self.prepare_priority_grant(
            seat,
            cleanup_frame=cleanup_frame is not None,
        )
        if plan is not None:
            self.commit_priority_grant(plan)

    def issue_priority(
        self, seat: str, hints: Mapping[str, Any] | None = None
    ) -> DecisionGroup:
        hints = dict(hints or self.host._priority_action_hints(seat))
        payload = {
            "stack": [
                {
                    "id": item.ref,
                    "label": item.label,
                    "controller": item.controller,
                }
                for item in reversed(self.state.stack)
            ],
            "legal": hints,
            "yield_modes": ["none", *_YIELD_MODES],
        }
        return self.host.permissions.issue(
            kind="priority",
            role="pilot",
            actors=[seat],
            allowed_actions=[
                "pass",
                "play_land",
                "cast",
                "activate",
                "turn_face_up",
                "undo_mana",
                "concede",
            ],
            payload_by_actor={seat: payload},
        )

    def complete_priority(self, decision: DecisionGroup) -> None:
        seat = decision.actors[0]
        response = dict(decision.responses[seat])
        action = response.pop("action")
        if action == "pass":
            clear_mana_undo_stack(self.state.players[seat].stats)
            self.set_yield(seat, response.get("yield"))
            self.pass_priority(seat)
        elif action == "play_land":
            execute_land_entry_priority_action(
                self.host,
                seat=seat,
                response=response,
                entry_action_id=decision.decision_id,
            )
        elif action in {"cast", "activate", "turn_face_up"}:
            execute_mana_choice_capable_priority_action(
                self.host,
                seat=seat,
                action=action,
                response=response,
                payment_id=decision.decision_id,
            )
        elif action == "undo_mana":
            try:
                undo_mana_activation(self.host, seat, response)
            except ManaUndoError as exc:
                raise GameRuleError(str(exc)) from exc
        elif action == "concede":
            clear_mana_undo_stack(self.state.players[seat].stats)
            if response.get("confirm_concede") is not True:
                raise GameRuleError(
                    "Concession requires explicit confirmation"
                )
            self.host._eliminate_players([seat], reason="conceded")
        else:
            raise GameRuleError(f"Unsupported priority action {action}")

    def complete_special_action(self, seat: str) -> None:
        """Stabilize one committed no-stack action before restoring priority."""

        if seat not in self.host.active_seats:
            raise GameRuleError("Special-action seat is no longer active")
        self.state.priority_player = None
        self.state.priority_passes = []
        if not self.host._stabilize():
            self.state.priority_player = seat

    def set_yield(self, seat: str, value: Any) -> None:
        mode = str(value or "none")
        if mode == "none":
            self.state.players[seat].yield_policy = YieldPolicy()
            return
        if mode not in _YIELD_MODES:
            raise GameRuleError(f"Unknown yield mode {mode}")
        signature = self.meaningful_action_signature(seat)
        self.state.players[seat].yield_policy = YieldPolicy(
            mode=mode,
            created_revision=self.state.revision,
            created_event_sequence=self.state.event_sequence,
            created_stack_change_epoch=self.yield_change_epoch("stack"),
            created_public_change_epoch=self.yield_change_epoch("public"),
            created_draw_epoch=self.yield_change_epoch("draw", seat),
            created_action_change_epoch=self.yield_change_epoch(
                "action", seat
            ),
            created_turn_sequence=self.state.turn_sequence,
            created_priority_epoch=self.state.priority_epoch,
            created_active_player=self.state.active_player,
            created_phase=self.state.phase,
            created_step=self.state.step,
            created_land_plays_remaining=self.state.players[
                seat
            ].land_plays_remaining,
            action_signature=signature,
            stack_signature=self.stack_signature(),
            note="Pilot-issued priority yield",
        )

    @staticmethod
    def signature_hash(value: Any) -> str:
        return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()

    def stack_signature(self) -> str:
        return self.signature_hash(
            [
                {
                    "ref": item.ref,
                    "kind": item.kind,
                    "controller": item.controller,
                    "source": item.source_object_id,
                    "card": item.card_object_id,
                    "semantic": item.semantic_key,
                    "targets": item.targets,
                    "modes": item.modes,
                    "x": item.x_value,
                }
                for item in self.state.stack
            ]
        )

    def meaningful_action_signature(
        self,
        seat: str,
        hints: Mapping[str, Any] | None = None,
    ) -> str:
        hints = dict(hints or self.host._priority_action_hints(seat))
        meaningful_actions = []
        ordinary_mana_ids = {
            f"activate:{item['s']}:{item['a']}"
            for item in hints.get("mana_abilities", [])
            if item not in hints.get("abilities", [])
        }
        for action in hints.get("actions", []):
            if action.get("id") in {"pass", "concede"} or action.get(
                "id"
            ) in ordinary_mana_ids:
                continue
            meaningful_actions.append(action_offer_signature_facts(action))
        payload: dict[str, Any] = {
            "algorithm": "meaningful-action-signature/v1",
            "actions": sorted(
                meaningful_actions,
                key=lambda item: stable_json(item),
            ),
        }
        decision = self.state.pending_decision
        if decision is not None and seat in decision.actors:
            payload["mandatory_or_optional_choice"] = {
                "kind": decision.kind,
                "allowed": list(decision.allowed_actions),
                "context": copy.deepcopy(
                    decision.payload_by_actor.get(seat, {})
                ),
            }
        return self.signature_hash(payload)

    def optimization_stats(self, seat: str) -> dict[str, Any]:
        telemetry = self.state.players[seat].stats.setdefault(
            "decision_optimization", {}
        )
        for key in (
            "priority_windows_considered",
            "pass_only_windows_skipped",
            "yield_covered_windows",
            "suppressed_empty_windows",
            "suppressed_meaningful_windows",
            "yields_invalidated_by_phase",
            "yields_invalidated_by_draw",
            "yields_invalidated_by_action_change",
            "yields_invalidated_by_stack",
            "yields_invalidated_by_public_change",
            "illegal_target_actions_prevented",
            "illegal_target_actions_advertised",
            "actions_removed_for_no_targets",
            "actions_removed_for_mode_target_failure",
            "target_candidates_generated",
            "target_submissions_rejected",
            "targets_became_illegal_on_resolution",
            "spells_countered_by_rules",
            "spells_countered_by_effect",
            "stack_interaction_windows_created",
            "stack_interaction_windows_auto_passed",
        ):
            telemetry.setdefault(key, 0)
        return telemetry

    def increment_optimization(self, seat: str, key: str) -> None:
        telemetry = self.optimization_stats(seat)
        telemetry[key] = int(telemetry.get(key, 0)) + 1

    def yield_stop_reason(
        self, seat: str, action_signature: str | None = None
    ) -> YieldInvalidationReason | None:
        policy = self.state.players[seat].yield_policy
        if policy.mode == "none":
            return "none"
        if (
            policy.stop_phase is not None
            and self.state.phase == policy.stop_phase
            and (
                policy.stop_step is None
                or self.state.step == policy.stop_step
            )
        ):
            return "phase"
        if self.state.active_player == seat and (
            policy.created_active_player != seat
            or policy.created_turn_sequence != self.state.turn_sequence
            or policy.created_priority_epoch != self.state.priority_epoch
            or (
                self.state.phase
                in {"precombat_main", "postcombat_main"}
                and (
                    policy.created_phase != self.state.phase
                    or policy.created_step != "main"
                )
            )
        ):
            return "phase"
        if policy.mode == "until_my_turn" and self.state.active_player == seat:
            return "phase"
        if policy.stack_signature != self.stack_signature():
            return "stack"
        if policy.created_stack_change_epoch != self.yield_change_epoch(
            "stack"
        ):
            return "stack"
        if policy.created_draw_epoch != self.yield_change_epoch("draw", seat):
            return "draw"
        if policy.created_action_change_epoch != self.yield_change_epoch(
            "action", seat
        ):
            return "action_change"
        if policy.created_public_change_epoch != self.yield_change_epoch(
            "public"
        ):
            return "public_change"
        if (
            policy.created_land_plays_remaining
            != self.state.players[seat].land_plays_remaining
        ):
            return "action_change"
        current_signature = action_signature or self.meaningful_action_signature(
            seat
        )
        if policy.action_signature != current_signature:
            return "action_change"
        if policy.mode == "auto_if_no_response" and self.signature_has_actions(
            seat
        ):
            return "action_change"
        return None

    def can_auto_pass(
        self,
        seat: str,
        *,
        action_signature: str,
        meaningful: bool,
    ) -> tuple[bool, YieldInvalidationReason | None]:
        policy = self.state.players[seat].yield_policy
        if policy.mode == "none":
            return False, None
        reason = self.yield_stop_reason(seat, action_signature)
        if reason is not None:
            self.state.players[seat].yield_policy = YieldPolicy()
            if reason != "none":
                self.increment_optimization(
                    seat, f"yields_invalidated_by_{reason}"
                )
            return False, reason
        if policy.mode == "auto_if_no_response" and meaningful:
            self.state.players[seat].yield_policy = YieldPolicy()
            self.increment_optimization(
                seat, "yields_invalidated_by_action_change"
            )
            return False, "action_change"
        return True, None

    def signature_has_actions(
        self, seat: str, hints: Mapping[str, Any] | None = None
    ) -> bool:
        hints = dict(hints or self.host._priority_action_hints(seat))
        return any(hints.get(key) for key in ("cast", "lands", "abilities"))

    def record_action_opportunity(
        self,
        seat: str,
        *,
        hints: Mapping[str, Any],
        action_signature: str,
        outcome: str,
        yield_invalidation: str | None = None,
    ) -> dict[str, Any]:
        self.state.opportunity_sequence += 1
        meaningful_ids = [
            action["id"]
            for action in hints.get("actions", [])
            if action.get("id") not in {"pass", "concede"}
            and action.get("kind") != "mana"
            and (
                action.get("kind") != "activate"
                or any(
                    item.get("s") == action.get("source")
                    and item.get("a") == action.get("ability")
                    for item in hints.get("abilities", [])
                )
            )
        ]
        diagnostics = copy.deepcopy(hints.get("diagnostic") or {})
        meaningful = bool(meaningful_ids)
        row = {
            "sequence": self.state.opportunity_sequence,
            "revision": self.state.revision,
            "event_sequence": self.state.event_sequence,
            "turn_sequence": self.state.turn_sequence,
            "active_player": self.state.active_player,
            "phase": self.state.phase,
            "step": self.state.step,
            "priority_epoch": self.state.priority_epoch,
            "seat": seat,
            "action_signature": action_signature,
            "action_signature_algorithm": "meaningful-action-signature/v1",
            "meaningful_action_ids": meaningful_ids,
            "meaningful_action_count": len(meaningful_ids),
            "meaningful_actions_exist": meaningful,
            "pilot_task_issued": outcome == "pilot_task_issued",
            "safe_yield_covered": outcome == "safe_yield",
            "pass_only_auto_pass": outcome == "pass_only_auto_pass",
            "ordered_plan_covered": outcome == "ordered_plan",
            "incorrectly_suppressed": outcome == "incorrectly_suppressed",
            "outcome": outcome,
            "yield_invalidated_by": yield_invalidation,
            "diagnostic": diagnostics,
        }
        self.state.action_opportunities.append(row)
        return row

    def prepare_priority_pass(self, seat: str) -> PriorityPassPlan:
        if self.state.priority_player != seat:
            raise GameRuleError(f"{seat} does not have priority")
        passes = tuple([*self.state.priority_passes, seat])
        if len(set(passes)) != len(passes):
            raise StateInvariantError("A priority round contains duplicate seats")
        complete = len(passes) >= len(self.host.active_seats)
        return PriorityPassPlan(
            seat=seat,
            passes=passes,
            next_seat=(None if complete else self.host._next_active_after(seat)),
            round_complete=complete,
            stack_waiting=bool(self.state.stack),
        )

    def commit_priority_pass(
        self, plan: PriorityPassPlan, *, automatic: bool = False
    ) -> None:
        if self.state.priority_player != plan.seat:
            raise StateInvariantError("Priority pass plan is stale")
        self.state.priority_passes = list(plan.passes)
        if not automatic:
            self.host._log(
                plan.seat,
                "priority.pass",
                f"{plan.seat} passed priority.",
                importance=0,
            )
        if plan.round_complete:
            self.state.priority_player = None
            self.state.priority_passes = []
            if plan.stack_waiting:
                self.host._prepare_stack_resolution()
            else:
                self.host._advance_step()
            return
        self.state.priority_player = plan.next_seat

    def pass_priority(self, seat: str, *, automatic: bool = False) -> None:
        self.commit_priority_pass(
            self.prepare_priority_pass(seat),
            automatic=automatic,
        )

    def manual_active_main_phase_window(self, seat: str) -> bool:
        return bool(
            self.state.config.manual_active_main_phase
            and seat == self.state.active_player
            and not self.state.stack
            and (self.state.phase, self.state.step)
            in {
                ("precombat_main", "main"),
                ("postcombat_main", "main"),
            }
        )

    def pump(self, *, max_transitions: int = 1000) -> None:
        for _ in range(max_transitions):
            if (
                self.state.game_over
                or self.state.pending_decision is not None
                or self.host._semantic_pause_annotation() is not None
            ):
                return
            if not self.state.started:
                return
            if (
                self.state.priority_player is None
                and self.host._active_cleanup_frame() is not None
            ):
                self.grant_priority(self.state.active_player)
                continue
            if self.state.priority_player is not None:
                seat = self.state.priority_player
                hints = self.host._priority_action_hints(seat)
                action_signature = self.meaningful_action_signature(seat, hints)
                meaningful = self.signature_has_actions(seat, hints)
                self.increment_optimization(seat, "priority_windows_considered")
                if self.state.stack:
                    self.increment_optimization(
                        seat,
                        (
                            "stack_interaction_windows_created"
                            if meaningful
                            else "stack_interaction_windows_auto_passed"
                        ),
                    )
                can_yield, invalidation = self.can_auto_pass(
                    seat,
                    action_signature=action_signature,
                    meaningful=meaningful,
                )
                if (
                    self.state.config.auto_pass_empty_priority
                    and not meaningful
                    and not self.manual_active_main_phase_window(seat)
                ):
                    self.increment_optimization(
                        seat, "pass_only_windows_skipped"
                    )
                    self.increment_optimization(
                        seat, "suppressed_empty_windows"
                    )
                    self.record_action_opportunity(
                        seat,
                        hints=hints,
                        action_signature=action_signature,
                        outcome="pass_only_auto_pass",
                        yield_invalidation=invalidation,
                    )
                    self.pass_priority(seat, automatic=True)
                    continue
                if can_yield:
                    self.increment_optimization(seat, "yield_covered_windows")
                    self.record_action_opportunity(
                        seat,
                        hints=hints,
                        action_signature=action_signature,
                        outcome="safe_yield",
                    )
                    self.pass_priority(seat, automatic=True)
                    continue
                row = self.record_action_opportunity(
                    seat,
                    hints=hints,
                    action_signature=action_signature,
                    outcome="pilot_task_issued",
                    yield_invalidation=invalidation,
                )
                decision = self.issue_priority(seat, hints)
                row["decision_id"] = decision.decision_id
                return
            self.host._enter_step()
        raise StateInvariantError("Automatic transition limit exceeded")
