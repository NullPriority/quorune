from __future__ import annotations

"""Canonical owner for turn selection and phase/step progression."""

import copy
from dataclasses import replace

from typing import Any, Mapping, Protocol, Sequence

from .errors import GameRuleError, StateInvariantError
from .continuous_effect_state import (
    expire_end_of_turn_continuous_effects,
    expire_next_turn_continuous_effects,
    next_turn_expiry_players,
)
from .control_effects import synchronize_control_effects
from .damage_prevention import expire_end_of_turn_damage_modifiers
from .impulse_access import expire_temporary_play_permissions
from .turn_skip_model import ScheduledTurnSkip, TurnBoundary, TurnSkipScope, TurnSkipSpec
from .rules.land_play_permissions import (
    reset_additional_land_play_permissions,
)
from .rules.next_spell_payment import expire_next_spell_payments
from .model import (
    CombatState,
    GameState,
    StackItem,
    TurnEntry,
    YieldPolicy,
)
from .turn_history import roll_turn_history
from .turn_priority_model import apnap_participants
from .trigger_processing import (
    clear_pending_trigger_batches,
    matching_delayed_triggers,
)
from .util import unique_preserving_order


TURN_STEPS: tuple[tuple[str, str], ...] = (
    ("beginning", "untap"),
    ("beginning", "upkeep"),
    ("beginning", "draw"),
    ("precombat_main", "main"),
    ("combat", "beginning_combat"),
    ("combat", "declare_attackers"),
    ("combat", "declare_blockers"),
    ("combat", "combat_damage"),
    ("combat", "end_combat"),
    ("postcombat_main", "main"),
    ("ending", "end_step"),
    ("ending", "cleanup"),
)

_COMBAT_END_INDEX = TURN_STEPS.index(("postcombat_main", "main"))


class TurnStepHost(Protocol):
    """Narrow engine port used by the turn scheduler."""

    state: GameState
    permissions: Any

    @property
    def active_seats(self) -> list[str]: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _next_active_after(self, seat: str) -> str: ...

    def _require_seat(self, seat: str, *, in_game: bool = False) -> None: ...

    def _increment_optimization(self, seat: str, key: str) -> None: ...

    def _expire_goad_designations(self, player: str, *, departed_turn: bool = False) -> None: ...

    def _clear_mana(self, *, reason: str) -> None: ...

    def _finish_combat_phase(self) -> None: ...

    def _stabilize(self) -> bool: ...

    def _grant_priority(self, seat: str | None) -> None: ...

    def change_control(
        self,
        object_id: str,
        controller: str,
        *,
        reason: str,
    ) -> None: ...

    def move_card(
        self,
        object_id: str,
        destination: str,
        *,
        reason: str,
        log: bool = True,
        semantic_events: bool = True,
    ) -> Any: ...

    def _enter_step(
        self,
        *,
        held_triggers: Sequence[StackItem] = (),
        phase: str | None = None,
        step: str | None = None,
        active: str | None = None,
    ) -> None: ...

    def _begin_draw_sequence(
        self,
        seat: str,
        count: int,
        *,
        reason: str,
        continuation: Mapping[str, Any] | None = None,
    ) -> None: ...

    def _complete_draw_step_entry(self, active: str) -> None: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> Any: ...


class TurnStepOwner:
    """Own deterministic turn and step state behind an engine facade."""

    def __init__(self, host: TurnStepHost):
        self._host = host

    @property
    def state(self) -> GameState:
        return self._host.state

    def start_game(self) -> None:
        from .opening_hand import begin_opening_hand_actions
        if begin_opening_hand_actions(self._host):
            return
        self.start_first_turn()

    def start_first_turn(self) -> None:
        if self.state.started:
            raise GameRuleError('The first turn has already begun')
        self.state.started = True
        first = self.state.turn_order[0]
        entry = TurnEntry(
            turn_id=self._host._next_ref("N"),
            player=first,
            extra=False,
            created_sequence=self.state.turn_sequence,
        )
        self._host._log(
            None,
            "game.start",
            f"The game began; {first} takes the first turn.",
            importance=3,
        )
        self.begin_turn(entry)

    def schedule_extra_turn(
        self,
        seat: str,
        *,
        source: str | None = None,
    ) -> TurnEntry:
        self._host._require_seat(seat, in_game=True)
        entry = TurnEntry(
            turn_id=self._host._next_ref("X"),
            player=seat,
            extra=True,
            source=source,
            created_sequence=self.state.turn_sequence,
        )
        # Most recently created extra turn is taken first.
        self.state.extra_turns.insert(0, entry)
        self._host._log(
            seat,
            "turn.extra.scheduled",
            f"{seat} received an extra turn after this one.",
            {"turn": entry.turn_id, "source": source},
            importance=2,
            changed_players=[seat],
        )
        return entry

    def schedule_skip(self, seat: str, spec: TurnSkipSpec) -> ScheduledTurnSkip | None:
        self._host._require_seat(seat, in_game=True)
        if spec.scope is TurnSkipScope.THIS_TURN and seat != self.state.active_player:
            return None
        previous = next((
            grant for grant in self.state.scheduled_turn_skips
            if grant.player == seat and grant.spec.boundary is TurnBoundary.TURN
        ), None) if spec.boundary is TurnBoundary.TURN else None
        if previous is None:
            grant = ScheduledTurnSkip(
                self._host._next_ref("TS"), seat, spec,
                self.state.turn_sequence if spec.scope is TurnSkipScope.THIS_TURN else None,
            )
            self.state.scheduled_turn_skips.append(grant)
        else:
            grant = replace(previous, spec=replace(spec, count=previous.spec.count + spec.count))
            index = self.state.scheduled_turn_skips.index(previous)
            self.state.scheduled_turn_skips[index] = grant
        self._host._log(
            seat, "turn.skip.scheduled", f"{seat} will skip {spec.boundary.value}.",
            {"grant": grant.grant_id, **spec.to_dict()}, importance=2, changed_players=[seat],
        )
        return grant

    def consume_skip(self, grant: ScheduledTurnSkip) -> None:
        if grant.spec.scope is TurnSkipScope.ALL_OF_NEXT_TURN:
            return
        index = self.state.scheduled_turn_skips.index(grant)
        if grant.spec.count == 1:
            self.state.scheduled_turn_skips.pop(index)
        else:
            self.state.scheduled_turn_skips[index] = replace(
                grant, spec=replace(grant.spec, count=grant.spec.count - 1)
            )

    def _departure_expiry_players(self) -> frozenset[str]:
        players = next_turn_expiry_players(self.state)
        if self.state.departed_turn_boundary_version == 1:
            players = players.union(
                designation.player for card in self.state.cards.values() for designation in card.goaded_by
            )
        return players

    def retire_departed_turns(self, seat: str) -> None:
        retain_boundary = seat in self._departure_expiry_players()
        if not retain_boundary:
            self.state.extra_turns = [entry for entry in self.state.extra_turns if entry.player != seat]
        if self.state.scheduled_turn_skips:
            self.state.scheduled_turn_skips = [
                grant for grant in self.state.scheduled_turn_skips
                if grant.player != seat or retain_boundary and grant.spec.boundary is TurnBoundary.TURN
            ]

    def _whole_turn_skip(self, seat: str) -> ScheduledTurnSkip | None:
        return next((grant for grant in self.state.scheduled_turn_skips
                     if grant.applies(seat, TurnBoundary.TURN, self.state.turn_sequence)), None)

    def _visit_departed_turn_boundary(self, seat: str) -> None:
        """CR 800.4k/m preserves the duration boundary of an eligible absent turn."""
        skip = self._whole_turn_skip(seat)
        if skip is not None:
            self.consume_skip(skip)
        else:
            expire_next_turn_continuous_effects(self.state, seat)
            if self.state.departed_turn_boundary_version == 1:
                self._host._expire_goad_designations(seat, departed_turn=True)
            self.retire_departed_turns(seat)

    def _bind_turn_skips(self, seat: str) -> None:
        if not self.state.scheduled_turn_skips:
            return
        self.state.scheduled_turn_skips = [
            replace(grant, turn_sequence=self.state.turn_sequence)
            if grant.player == seat and grant.spec.scope is TurnSkipScope.ALL_OF_NEXT_TURN else grant
            for grant in self.state.scheduled_turn_skips
            if grant.turn_sequence is None
        ]

    def _advance_normal_rotation(self, entry: TurnEntry) -> None:
        if entry.extra:
            return
        from .phasing import mark_departed_phasing_returns

        previous = self.state.last_normal_turn_player
        expiry_players = self._departure_expiry_players() if self.state.eliminated_players else frozenset()
        if previous is not None and expiry_players.intersection(self.state.eliminated_players):
            seats = self.state.turn_order
            for seat in apnap_participants(seats, seats, previous)[1:]:
                if seat == entry.player or self.state.players[seat].in_game:
                    break
                if seat in expiry_players:
                    self._visit_departed_turn_boundary(seat)
        mark_departed_phasing_returns(self.state, previous, entry.player)
        self.state.last_normal_turn_player = entry.player

    def _first_unskipped_turn(self, entry: TurnEntry) -> TurnEntry:
        while True:
            if entry.skip_steps:
                raise GameRuleError(
                    "Skipped-step turn entries are not implemented; the turn cannot begin"
                )
            player = self.state.players.get(entry.player)
            if player is None:
                raise StateInvariantError(f"Turn entry names unknown player {entry.player!r}")
            if player.in_game:
                grant = self._whole_turn_skip(entry.player)
                if grant is None:
                    return entry
                self.consume_skip(grant)
                self._advance_normal_rotation(entry)
                self._host._log(
                    entry.player, "turn.skip.applied", f"{entry.player} skipped a turn.",
                    {"grant": grant.grant_id, "turn": entry.turn_id, "extra": entry.extra},
                    importance=2, changed_players=[entry.player],
                )
            elif entry.player in self._departure_expiry_players():
                self._visit_departed_turn_boundary(entry.player)
            entry = self.select_next_turn()

    def next_normal_player(self) -> str:
        anchor = (
            self.state.last_normal_turn_player
            or self.state.turn_order[0]
        )
        return self._host._next_active_after(anchor)

    def select_next_turn(self) -> TurnEntry:
        while self.state.extra_turns:
            entry = self.state.extra_turns.pop(0)
            if self.state.players[entry.player].in_game:
                return entry
            if entry.player in self._departure_expiry_players():
                self._visit_departed_turn_boundary(entry.player)
        seat = self.next_normal_player()
        return TurnEntry(
            turn_id=self._host._next_ref("N"),
            player=seat,
            extra=False,
            created_sequence=self.state.turn_sequence,
        )

    def begin_turn(self, entry: TurnEntry) -> None:
        entry = self._first_unskipped_turn(entry)

        previous_active_player = (
            self.state.current_turn.player
            if self.state.current_turn is not None
            else None
        )
        self._advance_normal_rotation(entry)
        self.state.current_turn = entry
        self.state.active_player = entry.player
        expire_next_turn_continuous_effects(self.state, entry.player)
        self.state.turn_sequence += 1
        self._bind_turn_skips(entry.player)
        if self.state.turn_history is not None:
            self.state.turn_history = roll_turn_history(
                self.state.turn_history,
                next_turn_sequence=self.state.turn_sequence,
                previous_active_player=previous_active_player,
            )

        player = self.state.players[entry.player]
        player.stats.pop(
            "protection_from_everything_until_next_turn",
            None,
        )
        next_turn_controller = player.stats.pop(
            "next_turn_controlled_by",
            None,
        )
        if (
            next_turn_controller in self._host.active_seats
            and next_turn_controller != entry.player
        ):
            player.stats["turn_controlled_by"] = next_turn_controller
        else:
            player.stats.pop("turn_controlled_by", None)
        player.turns_begun += 1
        self._host._expire_goad_designations(entry.player)
        player.land_plays_remaining = 1
        reset_additional_land_play_permissions(self._host, player.seat)
        if player.yield_policy.mode != "none":
            self._host._increment_optimization(
                entry.player,
                "yields_invalidated_by_phase",
            )
        player.yield_policy = YieldPolicy()
        self.state.combat = CombatState()
        self.state.phase_index = 0
        self.state.priority_player = None
        self.state.priority_passes = []
        self._host._log(
            entry.player,
            "turn.begin",
            (
                f"Turn {self.state.turn_sequence} began for "
                f"{entry.player}{' (extra)' if entry.extra else ''}."
            ),
            {
                "turn_id": entry.turn_id,
                "extra": entry.extra,
                "source": entry.source,
            },
            importance=2,
            changed_players=[entry.player],
        )
        self.enter_step()

    def enter_step(
        self,
        *,
        held_triggers: Sequence[StackItem] = (),
        boundary_replacement: str | None = None,
    ) -> None:
        if not 0 <= self.state.phase_index < len(TURN_STEPS):
            raise StateInvariantError(
                f"Invalid turn-step index {self.state.phase_index}"
            )
        phase, step = TURN_STEPS[self.state.phase_index]
        active = self.state.active_player
        if active is None:
            raise StateInvariantError("A turn has no active player")
        if active not in self.state.players:
            raise StateInvariantError(
                f"A turn names unknown active player {active!r}"
            )
        from .turn_skip_replacements import apply_step_skip_replacement

        while True:
            skipped = apply_step_skip_replacement(
                self._host, active, step, held_triggers, selection=boundary_replacement
            )
            if skipped is None:
                return
            if not skipped:
                break
            boundary_replacement = None
            self.state.phase_index = (
                _COMBAT_END_INDEX if step == "beginning_combat" else self.state.phase_index + 1
            )
            phase, step = TURN_STEPS[self.state.phase_index]

        # CR 800.4j leaves the current turn in progress when its active player
        # leaves a multiplayer game.  The turn therefore retains that physical
        # player's identity through later step boundaries; priority ownership
        # independently advances to a remaining player.

        self.state.phase = phase
        self.state.step = step
        from .forecast import synchronize_forecast_reveals
        synchronize_forecast_reveals(self._host)
        self.state.priority_player = None
        self.state.priority_passes = []
        self._host._log(
            None,
            "step.begin",
            f"{self.state.turn_sequence}:{phase}/{step}.",
            importance=0,
        )
        if step == "beginning_combat":
            # CR 802.2 uses attack-multiple-players in the supported profile.
            # Unsupported CR 507.1 variants fail during game setup.
            self.state.combat = CombatState(
                damage_sequence_id=self._host._next_ref("CD"),
                defending_players=[
                    seat
                    for seat in self._host.active_seats
                    if seat != active
                ],
            )
        self._host._enter_step(
            held_triggers=held_triggers,
            phase=phase,
            step=step,
            active=active,
        )

    def enter_draw_step(self, active: str) -> None:
        """Perform CR 504's turn-based draw for the retained active player."""

        if active not in self._host.active_seats:
            self._host._log(
                active,
                "draw.skip",
                f"{active} left the game and did not take the turn-based draw.",
                {"reason": "active_player_left_game"},
                importance=0,
            )
            self._host._complete_draw_step_entry(active)
            return
        first_turn = self.state.turn_sequence == 1
        should_draw = (
            not first_turn
            or self.state.config.effective_first_player_draws(
                len(self.state.turn_order)
            )
        )
        if self.state.config.auto_draw and should_draw:
            self._host._begin_draw_sequence(
                active,
                1,
                reason="turn-based draw",
                continuation={"kind": "turn_draw", "seat": active},
            )
            return
        if not should_draw:
            self._host._log(
                active,
                "draw.skip",
                f"{active} skipped the first-turn draw.",
                importance=0,
            )
        self._host._complete_draw_step_entry(active)

    def advance_step(
        self,
        *,
        held_triggers: Sequence[StackItem] = (),
    ) -> None:
        self._host._clear_mana(reason="step or phase ended")
        if (self.state.phase, self.state.step) == (
            "combat",
            "end_combat",
        ):
            self._host._finish_combat_phase()
        if (
            (self.state.phase, self.state.step)
            == ("combat", "declare_attackers")
            and not (
                self.state.combat.had_attacking_creature
                or self.state.combat.attackers
            )
        ):
            self.state.phase_index = TURN_STEPS.index(
                ("combat", "end_combat")
            )
            self.enter_step()
            return
        if (
            (self.state.phase, self.state.step)
            == ("combat", "combat_damage")
            and self.state.combat.first_strike_step
            and self.state.combat.damage_step_index == 0
        ):
            self.state.combat.damage_step_index = 1
            self.enter_step(held_triggers=held_triggers)
            return
        self.state.phase_index += 1
        if self.state.phase_index >= len(TURN_STEPS):
            if (self.state.phase, self.state.step) == (
                "ending",
                "cleanup",
            ):
                self.state.phase_index = TURN_STEPS.index(
                    ("ending", "cleanup")
                )
                self.enter_step()
                return
            self.finish_cleanup()
            return
        self.enter_step(held_triggers=held_triggers)

    def active_cleanup_frame(self) -> dict[str, Any] | None:
        return next(
            (
                annotation
                for annotation in reversed(self.state.annotations)
                if annotation.get("kind") == "cleanup_exception_frame"
                and annotation.get("active", False)
            ),
            None,
        )

    def remove_cleanup_frames(self) -> None:
        self.state.annotations = [
            annotation
            for annotation in self.state.annotations
            if annotation.get("kind") != "cleanup_exception_frame"
        ]

    def finish_cleanup(self) -> None:
        active = self.state.active_player
        in_cleanup_step = (self.state.phase, self.state.step) == (
            "ending",
            "cleanup",
        )
        self.remove_cleanup_frames()
        cleanup_iteration = 1 + sum(
            event.code == "turn.cleanup"
            and event.turn_sequence == self.state.turn_sequence
            for event in self.state.events
        )
        cleanup_delayed = (
            matching_delayed_triggers(
                self._host,
                "step.begin",
                {
                    "phase": "ending",
                    "step": "cleanup",
                    "player": active,
                },
            )
            if in_cleanup_step
            else []
        )
        frame = {
            "kind": "cleanup_exception_frame",
            "active": True,
            "turn_sequence": self.state.turn_sequence,
            "active_player": active,
            "iteration": cleanup_iteration,
            "delayed_trigger_ids": [
                trigger.trigger_id for trigger in cleanup_delayed
            ],
            "delayed_triggers_queued": False,
            "priority_granted": False,
            "exception_reasons": [],
        }
        if in_cleanup_step:
            self.state.annotations.append(frame)
        expire_temporary_play_permissions(
            self.state,
            active_player=active,
        )
        for card in self.state.cards.values():
            card.marked_damage = 0
            card.deathtouch_damage = False
            card.regeneration_shields = 0
            card.temporary_keywords.clear()
            card.attacking = None
            card.blocking = None
            until_end = dict(
                card.annotations.get("until_end_of_turn") or {}
            )
            if "copy_overrides_previous" in until_end:
                previous = until_end["copy_overrides_previous"]
                if previous is None:
                    card.annotations.pop("copy_overrides", None)
                else:
                    card.annotations["copy_overrides"] = copy.deepcopy(
                        previous
                    )
            previous_controller = until_end.get("control_previous")
            if (
                previous_controller in self.state.players
                and card.zone == "battlefield"
                and card.controller != previous_controller
            ):
                self._host.change_control(
                    card.object_id,
                    str(previous_controller),
                    reason="temporary control effect ended",
                )
            card.annotations.pop("until_end_of_turn", None)
        for player in self.state.players.values():
            expire_next_spell_payments(player.stats)
            player.stats.pop("next_spell_uncounterable", None)
            player.stats.pop(
                "spells_cant_be_countered_until_end",
                None,
            )
            player.stats.pop("hexproof_from_colors_until_end", None)
        expire_end_of_turn_damage_modifiers(self.state)
        expire_end_of_turn_continuous_effects(self.state)
        synchronize_control_effects(self._host, reason="temporary control effect ended")
        self._host._clear_mana(reason="cleanup")
        self._host._log(
            active,
            "turn.cleanup",
            f"{active} completed cleanup.",
            importance=0,
        )
        if active in self.state.players:
            self.state.players[active].stats.pop(
                "turn_controlled_by",
                None,
            )
        if self.state.game_over:
            self.remove_cleanup_frames()
            return
        if in_cleanup_step:
            before_stabilize_event = self.state.event_sequence
            waiting = self._host._stabilize()
            stabilization_events = [
                event
                for event in self.state.events
                if event.event_id > before_stabilize_event
            ]
            reasons: list[str] = []
            if cleanup_delayed:
                reasons.append("cleanup_trigger")
            if waiting:
                reasons.append("state_or_trigger_choice")
            if any(
                event.code.startswith("state.")
                or event.code == "player.eliminated"
                for event in stabilization_events
            ):
                reasons.append("state_based_action")
            if (
                self.state.stack
                or self.state.pending_trigger_batches
                or any(
                    event.code == "stack.trigger"
                    for event in stabilization_events
                )
            ):
                reasons.append("trigger_waiting")
            frame["exception_reasons"] = unique_preserving_order(reasons)
            if reasons:
                self._host._log(
                    active,
                    "cleanup.priority_required",
                    (
                        "Cleanup created a state action or waiting "
                        "trigger; the active player receives priority."
                    ),
                    {
                        "iteration": cleanup_iteration,
                        "reasons": frame["exception_reasons"],
                    },
                    importance=2,
                    changed_players=[active] if active else [],
                )
                if waiting:
                    return
                self._host._grant_priority(active)
                return
            self.remove_cleanup_frames()
        self.begin_turn(self.select_next_turn())

    def end_turn_now(self, *, actor: str, reason: str) -> None:
        """Perform the represented special action sequence for ending a turn."""

        exiled_cards: list[str] = []
        for stack_item in list(self.state.stack):
            if not stack_item.card_object_id:
                continue
            card = self.state.cards.get(stack_item.card_object_id)
            if card is None or card.zone != "stack":
                continue
            self._host.move_card(
                card.object_id,
                "exile",
                reason=reason,
                log=False,
                semantic_events=False,
            )
            exiled_cards.append(card.object_id)
        removed_stack = [item.ref for item in self.state.stack]
        self.state.stack.clear()
        clear_pending_trigger_batches(self._host)
        self._host.permissions.invalidate_current()
        self.state.priority_player = None
        self.state.priority_passes = []
        self.state.combat = CombatState()
        self._host._log(
            actor,
            "turn.ended",
            f"{actor} ended the turn.",
            {
                "stack_objects_exiled": removed_stack,
                "cards_exiled": [
                    self.state.cards[object_id].ref
                    for object_id in exiled_cards
                ],
                "reason": reason,
            },
            importance=3,
            changed_objects=exiled_cards,
        )
        self.state.phase_index = TURN_STEPS.index(
            ("ending", "cleanup")
        )
        self.enter_step()
