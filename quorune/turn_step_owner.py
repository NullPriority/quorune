from __future__ import annotations

"""Canonical owner for turn selection and phase/step progression."""

import copy
from dataclasses import replace

from typing import Any, Mapping, Protocol, Sequence

from .additional_beginning_model import AdditionalBeginningPhases
from .extra_combat_model import (
    MAIN_PHASE_COMBAT_GRANT_FIELD, MainPhaseCombatSchedule, MainPhaseInsertion, own_main_phase_for_combat,
)
from .errors import GameRuleError, StateInvariantError
from .continuous_effect_state import (
    expire_end_of_turn_continuous_effects,
    expire_next_turn_continuous_effects,
    expire_recipient_controller_untap_effects,
    next_turn_expiry_players,
)
from .control_effects import synchronize_control_effects
from .damage_prevention import expire_end_of_turn_damage_modifiers
from .impulse_access import expire_next_end_step_play_permissions, expire_temporary_play_permissions
from .opponent_library_exile import expire_opponent_library_exile_play_permissions
from .turn_skip_model import ScheduledTurnSkip, TurnBoundary, TurnSkipScope, TurnSkipSpec
from .rules.land_play_permissions import (
    reset_additional_land_play_permissions,
)
from .rules.next_spell_payment import expire_temporary_cast_ability_grants
from .model import (
    GameState,
    StackItem,
    TurnEntry,
    YieldPolicy,
)
from .turn_history import roll_turn_history
from .turn_priority_model import (
    NEXT_TURN_CONTROL_LIFETIME_VERSION_FIELD, TURN_CONTROL_LIFETIME_VERSION_FIELD,
    apnap_participants, read_turn_control_lifetime_version,
)
from .trigger_processing import (
    clear_pending_trigger_batches,
    expire_controlled_combat_token_triggers,
    expire_next_cast_copy_triggers,
    expire_target_death_return_triggers,
    expire_watched_combat_draw_triggers,
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

_COMBAT_START_INDEX = TURN_STEPS.index(("combat", "beginning_combat"))
_COMBAT_END_INDEX = TURN_STEPS.index(("postcombat_main", "main"))
_END_TURN_CLEANUP_PENDING = "end_turn_cleanup_pending"


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

    def _move_cards_simultaneously(
        self, changes: Sequence[tuple[str, str]], *, reason: str,
    ) -> Sequence[Any]: ...

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

    def require_current_combat(self) -> None:
        if self.state.phase != "combat" or self.state.current_turn is None:
            raise GameRuleError("An extra combat requires a current combat phase")

    def in_current_own_combat(self, actor: str) -> bool:
        return (self.state.current_turn is not None and self.state.phase == "combat"
                and self.state.active_player == actor)

    def schedule_extra_combat(self, actor: str, *, source: str) -> None:
        self._host._require_seat(actor, in_game=True)
        self.require_current_combat()
        self.state.extra_combat_phases += 1
        self._host._log(
            actor,
            "combat.extra.scheduled",
            "An additional combat phase will follow this phase.",
            {"source": source, "active_player": self.state.active_player,
             "queued_phases": self.state.extra_combat_phases},
            importance=2,
        )

    def schedule_main_phase_combat(self, actor: str, *, source: str, grant: str, follow_with_main: bool = False) -> None:
        self._host._require_seat(actor, in_game=True)
        phase_owner = self.state.active_player if follow_with_main else actor
        if not own_main_phase_for_combat(self.state, phase_owner):
            raise GameRuleError("The granted combat must follow the resolving controller's main phase")
        staged = (MainPhaseInsertion("combat", grant),)
        if follow_with_main:
            staged += (MainPhaseInsertion("main"),)
        queue = self.state.main_phase_extra_combats
        if queue is None:
            beginning = self.state.additional_beginning_phases if self.state.phase == "postcombat_main" else None
            earlier = (MainPhaseInsertion("beginning"),) * beginning.pending if beginning is not None else ()
            self.state.main_phase_extra_combats = MainPhaseCombatSchedule(
                self.state.phase, self.state.turn_sequence, (*staged, *earlier),
            )
        else:
            self._require_current_main_insertion(queue)
            self.state.main_phase_extra_combats = replace(queue, pending=(*staged, *queue.pending))
        self._host._log(
            actor, "combat.extra.scheduled",
            ("An additional combat and main phase will follow this main phase." if follow_with_main
             else "An additional combat phase will follow this main phase."),
            {"source": source, "grant": grant, "after_phase": self.state.phase,
             **({"main_phase_count": 1} if follow_with_main else {})},
            importance=2,
        )

    def _require_current_main_insertion(self, queue: MainPhaseCombatSchedule) -> None:
        expected = "postcombat_main" if queue.engaged and queue.reached_added_main else queue.after_phase
        if (queue.turn_sequence != self.state.turn_sequence or self.state.phase != expected
                or queue.engaged and not queue.reached_added_main):
            raise StateInvariantError("The scheduled phases belong to a different main-phase boundary")

    def schedule_additional_beginning(self, player: str, *, source: str) -> None:
        if (
            self.state.active_player != player
            or (self.state.phase, self.state.step) != ("postcombat_main", "main")
            or type(source) is not str or not source
        ):
            raise GameRuleError("An additional beginning phase must follow the current postcombat main")
        queue = self.state.main_phase_extra_combats
        if queue is not None:
            self._require_current_main_insertion(queue)
            self.state.main_phase_extra_combats = replace(
                queue, pending=(MainPhaseInsertion("beginning"), *queue.pending),
            )
        self.state.additional_beginning_phases = (
            self.state.additional_beginning_phases.queue_one()
            if self.state.additional_beginning_phases is not None else AdditionalBeginningPhases(1)
        )
        self._host._log(
            player, "phase.additional_beginning",
            f"{player} will take an additional beginning phase.",
            {"source": source, "queued_phases": self.state.additional_beginning_phases.pending},
            importance=1,
        )

    def _next_start_or_end_index(self, phase: str, step: str) -> int:
        queue = self.state.main_phase_extra_combats
        extra = self.state.additional_beginning_phases
        if queue is not None and (
            (phase, step) == (queue.after_phase, "main")
            or queue.engaged and queue.reached_added_main and (phase, step) == ("postcombat_main", "main")
            or queue.engaged and extra is not None and extra.active and (phase, step) == ("beginning", "draw")
        ):
            return self._next_main_phase_insertion()
        if extra is not None and (
            (phase, step) == ("postcombat_main", "main")
            or (extra.active and (phase, step) == ("beginning", "draw"))
        ):
            if extra.pending:
                self.state.additional_beginning_phases = extra.begin_next()
                return TURN_STEPS.index(("beginning", "untap"))
            self.state.additional_beginning_phases = None
            return TURN_STEPS.index(("ending", "end_step"))
        return self.state.phase_index + 1

    def _next_main_phase_insertion(self) -> int:
        queue = self.state.main_phase_extra_combats
        if queue is None or queue.turn_sequence != self.state.turn_sequence:
            raise StateInvariantError("The main-phase insertion lost its original turn")
        extra = self.state.additional_beginning_phases
        if extra is not None and extra.active:
            extra = AdditionalBeginningPhases(extra.pending) if extra.pending else None
            self.state.additional_beginning_phases = extra
        if not queue.pending:
            self.state.main_phase_extra_combats = None
            if queue.after_phase == "precombat_main":
                return _COMBAT_START_INDEX
            return TURN_STEPS.index(("ending", "end_step"))
        first, *remaining = queue.pending
        self.state.main_phase_extra_combats = replace(
            queue, pending=tuple(remaining), engaged=True, combat_grant=first.grant,
            reached_added_main=queue.reached_added_main or first.kind == "main",
        )
        if first.kind == "combat":
            return _COMBAT_START_INDEX
        if first.kind == "main":
            self.state.inserted_main_phase_count += 1
            return _COMBAT_END_INDEX
        if extra is None or not extra.pending:
            raise StateInvariantError("The additional beginning phase lost its canonical queue")
        self.state.additional_beginning_phases = extra.begin_next()
        return TURN_STEPS.index(("beginning", "untap"))

    def _cancel_main_combat_delays(self, grants: set[str]) -> None:
        for trigger in self.state.delayed_triggers:
            if trigger.active and trigger.condition.get(MAIN_PHASE_COMBAT_GRANT_FIELD) in grants:
                trigger.active = False

    def _clear_main_phase_combats(self) -> None:
        queue = self.state.main_phase_extra_combats
        if queue is not None:
            grants = {entry.grant for entry in queue.pending if entry.grant is not None}
            if queue.combat_grant is not None:
                grants.add(queue.combat_grant)
            self._cancel_main_combat_delays(grants)
            self.state.main_phase_extra_combats = None

    def _phase_index_after_combat(self) -> int:
        queue = self.state.main_phase_extra_combats
        if queue is not None and queue.engaged and queue.combat_grant is not None:
            self._cancel_main_combat_delays({queue.combat_grant})
            self.state.main_phase_extra_combats = replace(queue, combat_grant=None)
        if self.state.extra_combat_phases:
            self.state.extra_combat_phases -= 1
            return _COMBAT_START_INDEX
        if queue is not None and queue.engaged:
            return self._next_main_phase_insertion()
        return _COMBAT_END_INDEX

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

    def _first_unskipped_turn(self, entry: TurnEntry, *, boundary_replacement=None) -> TurnEntry | None:
        from .turn_skip_replacements import apply_turn_start_skip_replacement

        while True:
            if entry.skip_steps:
                raise GameRuleError(
                    "Skipped-step turn entries are not implemented; the turn cannot begin"
                )
            player = self.state.players.get(entry.player)
            if player is None:
                raise StateInvariantError(f"Turn entry names unknown player {entry.player!r}")
            if player.in_game:
                if entry.extra:
                    skipped = apply_turn_start_skip_replacement(
                        self._host, entry, selection=boundary_replacement,
                    )
                    if skipped is None:
                        return None
                    boundary_replacement = None
                    if skipped:
                        entry = self.select_next_turn()
                        continue
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

    def begin_turn(self, entry: TurnEntry, *, boundary_replacement=None) -> None:
        entry = self._first_unskipped_turn(entry, boundary_replacement=boundary_replacement)
        if entry is None:
            return

        previous_active_player = (
            self.state.current_turn.player
            if self.state.current_turn is not None
            else None
        )
        if previous_active_player in self.state.players:
            previous_stats = self.state.players[previous_active_player].stats
            if read_turn_control_lifetime_version(previous_stats) is not None:
                previous_stats.pop(TURN_CONTROL_LIFETIME_VERSION_FIELD)
                previous_stats.pop("turn_controlled_by", None)
        self._advance_normal_rotation(entry)
        self.state.current_turn = entry
        self.state.active_player = entry.player
        self.state.reset_upkeep_completion()
        expire_next_turn_continuous_effects(self.state, entry.player)
        self.state.turn_sequence += 1
        expire_controlled_combat_token_triggers(self._host)
        expire_next_cast_copy_triggers(self._host)
        expire_target_death_return_triggers(self._host)
        expire_watched_combat_draw_triggers(self._host)
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
        lifetime = read_turn_control_lifetime_version(player.stats, NEXT_TURN_CONTROL_LIFETIME_VERSION_FIELD)
        player.stats.pop(NEXT_TURN_CONTROL_LIFETIME_VERSION_FIELD, None)
        player.stats.pop(TURN_CONTROL_LIFETIME_VERSION_FIELD, None)
        next_turn_controller = player.stats.pop(
            "next_turn_controlled_by",
            None,
        )
        if (
            next_turn_controller in self._host.active_seats
            and next_turn_controller != entry.player
        ):
            player.stats["turn_controlled_by"] = next_turn_controller
            if lifetime is not None:
                player.stats[TURN_CONTROL_LIFETIME_VERSION_FIELD] = lifetime
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
        self.state.combat = self.state.combat.fresh()
        self.state.extra_combat_phases = 0
        self._clear_main_phase_combats()
        self.state.inserted_main_phase_count = 0
        self.state.additional_beginning_phases = None
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
        pending_end = next((row for row in self.state.annotations if row.get("kind") == _END_TURN_CLEANUP_PENDING), None)
        if pending_end is not None and not self._finish_pending_turn_end(pending_end):
            return
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
                self._phase_index_after_combat() if step == "beginning_combat" else self._next_start_or_end_index(phase, step)
            )
            phase, step = TURN_STEPS[self.state.phase_index]

        # CR 800.4j leaves the current turn in progress when its active player
        # leaves a multiplayer game.  The turn therefore retains that physical
        # player's identity through later step boundaries; priority ownership
        # independently advances to a remaining player.

        self.state.phase = phase
        self.state.step = step
        if step == "untap":
            expire_recipient_controller_untap_effects(self.state, active)
        elif step == "end_step":
            expire_next_end_step_play_permissions(self.state, active_player=active)
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
            self.state.combat = self.state.combat.fresh(
                damage_sequence_id=self._host._next_ref("CD"),
                defending_players=[
                    seat
                    for seat in self._host.active_seats
                    if seat != active
                ],
            )
        from .rad_counters import collect_rad_trigger

        self._host._enter_step(
            held_triggers=(*held_triggers, *collect_rad_trigger(self._host, active, phase, step)),
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
        extra = self.state.additional_beginning_phases
        first_turn = self.state.turn_sequence == 1 and not (extra is not None and extra.active)
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
        if (self.state.phase, self.state.step) == ("beginning", "upkeep"):
            self.state.complete_upkeep()
        if (self.state.phase, self.state.step) == (
            "combat",
            "end_combat",
        ):
            self._host._finish_combat_phase()
            self.state.phase_index = self._phase_index_after_combat()
            self.enter_step(held_triggers=held_triggers)
            return
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
        self.state.phase_index = self._next_start_or_end_index(self.state.phase, self.state.step)
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
        active_stats = self.state.players[active].stats if active in self.state.players else None
        lifetime = read_turn_control_lifetime_version(active_stats) if active_stats is not None else None
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
        expire_opponent_library_exile_play_permissions(self.state, active_player=active)
        from .entry_results import expire_copy_scope
        from .replacement.face_status_copy import CopyExpiry

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
            expire_copy_scope(card, CopyExpiry.END_OF_TURN)
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
        from .qualified_hexproof import TURN_PLAYER_COLOR_HEXPROOF

        for player in self.state.players.values():
            expire_temporary_cast_ability_grants(player.stats)
            player.stats.pop(TURN_PLAYER_COLOR_HEXPROOF, None)
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
        if active_stats is not None and (lifetime is None or self.state.game_over):
            active_stats.pop("turn_controlled_by", None)
            active_stats.pop(TURN_CONTROL_LIFETIME_VERSION_FIELD, None)
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

    def _finish_pending_turn_end(self, pending: dict[str, Any]) -> bool:
        """Resume the pre-cleanup state-based check after a required choice."""
        if (pending != {"kind": _END_TURN_CLEANUP_PENDING, "schema_version": 1, "turn_sequence": self.state.turn_sequence}
                or type(pending["schema_version"]) is not int or type(pending["turn_sequence"]) is not int
                or self.state.stack or self.state.priority_player is not None):
            raise StateInvariantError("The pending turn ending no longer matches the current turn")
        if self._host._stabilize() and not self.state.game_over:
            return False
        self.state.annotations.remove(pending)
        if self.state.game_over:
            return False
        self._host._clear_mana(reason="step or phase ended")
        if self.state.phase == "combat":
            self._host._finish_combat_phase()
        self.state.extra_combat_phases = 0
        self._clear_main_phase_combats()
        self.state.additional_beginning_phases = None
        self.state.phase_index = TURN_STEPS.index(("ending", "cleanup"))
        return True

    def end_turn_now(self, *, actor: str, reason: str, check_state_actions: bool = False) -> None:
        """End the current turn through stack exile and cleanup."""
        stack = tuple(self.state.stack)
        exiled_cards = [
            item.card_object_id for item in stack
            if item.card_object_id and (card := self.state.cards.get(item.card_object_id)) is not None
            and card.zone == "stack"
        ]
        if check_state_actions:
            from .combat_entry_activations import cleanup_ninjutsu_reveal
            from .effect_runtime.hand_inspection import cleanup_stack_cost_hand_reveals
            from .spell_resolution_names import finish_spell_resolution_names

            clear_pending_trigger_batches(self._host)
            self._host._move_cards_simultaneously([(object_id, "exile") for object_id in exiled_cards], reason=reason)
            for item in stack:
                cleanup_ninjutsu_reveal(self._host, item)
                cleanup_stack_cost_hand_reveals(self._host, item)
                finish_spell_resolution_names(self._host, item)
            self.remove_cleanup_frames()
        else:
            for object_id in exiled_cards:
                self._host.move_card(object_id, "exile", reason=reason, log=False, semantic_events=False)
        removed_stack = [item.ref for item in self.state.stack]
        self.state.stack.clear()
        if not check_state_actions:
            clear_pending_trigger_batches(self._host)
        self._host.permissions.invalidate_current()
        self.state.priority_player = None
        self.state.priority_passes = []
        if not check_state_actions:
            self.state.combat = self.state.combat.fresh()
            self.state.extra_combat_phases = 0
            self._clear_main_phase_combats()
            self.state.additional_beginning_phases = None
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
        if check_state_actions:
            self.state.annotations.append({
                "kind": _END_TURN_CLEANUP_PENDING, "schema_version": 1, "turn_sequence": self.state.turn_sequence,
            })
        else:
            self.state.phase_index = TURN_STEPS.index(("ending", "cleanup"))
        self.enter_step()
