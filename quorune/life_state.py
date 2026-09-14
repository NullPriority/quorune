from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .state_planner import (
    apply_state_plan,
    commit_state_plan,
    plan_state_changes,
    validate_state_plan,
)


class LifeStateError(ValueError):
    """A typed life-total change cannot be planned or committed exactly."""


class LifePlayerState(Protocol):
    life: int


class LifeStateView(Protocol):
    players: Mapping[str, LifePlayerState]

    def active_seats(self) -> Sequence[str]: ...


class LifeStateHost(Protocol):
    state: LifeStateView


def _record_life_loss(
    host: LifeStateHost,
    transitions: Sequence["LifeTransition"],
) -> None:
    recorder = getattr(host, "_record_turn_history", None)
    if not callable(recorder):
        return
    for transition in transitions:
        amount = transition.before - transition.after
        if amount > 0:
            recorder(
                "player_lost_life",
                target=transition.player,
                target_kind="player",
                amount=amount,
            )


@dataclass(frozen=True, slots=True)
class LifeChange:
    player: str
    amount: int

    def __post_init__(self) -> None:
        if not self.player:
            raise LifeStateError("Life changes require a player")
        if type(self.amount) is not int:
            raise LifeStateError("Life change amounts must be integers")


@dataclass(frozen=True, slots=True)
class LifeTransition:
    player: str
    requested_delta: int
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class LifeStatePlan:
    transitions: tuple[LifeTransition, ...]

    @property
    def changed_players(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    transition.player
                    for transition in self.transitions
                    if transition.before != transition.after
                }
            )
        )


@dataclass(frozen=True, slots=True)
class PreparedLifePayment:
    """A nonreplaceable CR 119.4 life payment pinned before mutation."""

    player: str
    amount: int
    plan: LifeStatePlan

    def __post_init__(self) -> None:
        if type(self.player) is not str or not self.player:
            raise LifeStateError("Life payments require a player")
        if type(self.amount) is not int or self.amount < 0:
            raise LifeStateError(
                "Life payment amounts must be nonnegative integers"
            )
        if not isinstance(self.plan, LifeStatePlan):
            raise LifeStateError("Life payments require a typed state plan")


def _current_life(host: LifeStateHost, player: str) -> int:
    state = host.state.players.get(player)
    if state is None or player not in host.state.active_seats():
        raise LifeStateError("Life-change player is not active")
    return int(state.life)


class _LifeAdapter:
    @staticmethod
    def validate_change(change: LifeChange) -> None:
        if not isinstance(change, LifeChange):
            raise LifeStateError("Life plans require typed changes")

    @staticmethod
    def key(change: LifeChange) -> str:
        return change.player

    @staticmethod
    def current_value(host: LifeStateHost, change: LifeChange) -> int:
        return _current_life(host, change.player)

    @staticmethod
    def next_value(before: int, change: LifeChange) -> int:
        return before + change.amount

    @staticmethod
    def transition(
        change: LifeChange, *, before: int, after: int
    ) -> LifeTransition:
        return LifeTransition(change.player, change.amount, before, after)

    @staticmethod
    def change_from_transition(transition: LifeTransition) -> LifeChange:
        return LifeChange(transition.player, transition.requested_delta)

    @staticmethod
    def transition_before(transition: LifeTransition) -> int:
        return transition.before

    @staticmethod
    def transition_after(transition: LifeTransition) -> int:
        return transition.after

    @staticmethod
    def validate_transition(transition: LifeTransition) -> None:
        if not isinstance(transition, LifeTransition):
            raise LifeStateError("Life commits require typed transitions")
        if transition.after != transition.before + transition.requested_delta:
            raise LifeStateError("Life transition arithmetic is invalid")

    @staticmethod
    def apply_final(host: LifeStateHost, transition: LifeTransition) -> None:
        host.state.players[transition.player].life = transition.after


_LIFE_ADAPTER = _LifeAdapter()


def _life_planner_error(error: ValueError) -> LifeStateError:
    if str(error) == "State plan is stale":
        return LifeStateError("Life plan is stale")
    if str(error) == "State plan changed before commit":
        return LifeStateError("Life plan changed before commit")
    return LifeStateError(str(error))


def plan_life_changes(
    host: LifeStateHost,
    changes: Sequence[LifeChange],
) -> LifeStatePlan:
    """Validate and aggregate a simultaneous life-change batch."""

    return LifeStatePlan(plan_state_changes(host, changes, _LIFE_ADAPTER))


def validate_life_changes(host: LifeStateHost, plan: LifeStatePlan) -> None:
    """Fail before mutation if any planned life total is stale."""

    if not isinstance(plan, LifeStatePlan):
        raise LifeStateError("Life commits require a typed plan")
    try:
        validate_state_plan(host, plan.transitions, _LIFE_ADAPTER)
    except ValueError as exc:
        if isinstance(exc, LifeStateError):
            raise
        raise _life_planner_error(exc) from exc


def apply_life_changes(
    host: LifeStateHost,
    plan: LifeStatePlan,
) -> tuple[LifeTransition, ...]:
    """Apply a life plan after the caller completed precommit validation."""

    transitions = apply_state_plan(host, plan.transitions, _LIFE_ADAPTER)
    _record_life_loss(host, transitions)
    return transitions


def commit_life_changes(
    host: LifeStateHost,
    plan: LifeStatePlan,
) -> tuple[LifeTransition, ...]:
    """Validate and commit one typed life-total batch."""

    if not isinstance(plan, LifeStatePlan):
        raise LifeStateError("Life commits require a typed plan")
    try:
        transitions = commit_state_plan(host, plan.transitions, _LIFE_ADAPTER)
        _record_life_loss(host, transitions)
        return transitions
    except ValueError as exc:
        if isinstance(exc, LifeStateError):
            raise
        raise _life_planner_error(exc) from exc


def pay_life_cost(
    host: LifeStateHost,
    player: str,
    amount: int,
) -> LifeTransition:
    """Pay a nonreplaceable life cost through the canonical state owner."""

    prepared = prepare_life_payment(host, player, amount)
    return commit_life_payment(host, prepared)


def prepare_life_payment(
    host: LifeStateHost,
    player: str,
    amount: int,
) -> PreparedLifePayment:
    """Pin a payable CR 119.4 subtraction without changing life totals."""

    if type(amount) is not int or amount < 0:
        raise LifeStateError(
            "Life payment amounts must be nonnegative integers"
        )
    if _current_life(host, player) < amount:
        raise LifeStateError("Cannot pay more life than the player has")
    plan = plan_life_changes(host, (LifeChange(player, -amount),))
    return PreparedLifePayment(player=player, amount=amount, plan=plan)


def validate_life_payment(
    host: LifeStateHost,
    prepared: PreparedLifePayment,
) -> None:
    """Reject malformed, unaffordable, or stale prepared payments."""

    if not isinstance(prepared, PreparedLifePayment):
        raise LifeStateError("Life payment commits require a typed plan")
    transitions = prepared.plan.transitions
    if len(transitions) != 1:
        raise LifeStateError("Life payment plans require one transition")
    transition = transitions[0]
    if (
        transition.player != prepared.player
        or transition.requested_delta != -prepared.amount
        or transition.before < prepared.amount
    ):
        raise LifeStateError("Life payment plan does not match its request")
    validate_life_changes(host, prepared.plan)


def commit_life_payment(
    host: LifeStateHost,
    prepared: PreparedLifePayment,
) -> LifeTransition:
    """Commit a replay-validated nonreplaceable life payment."""

    validate_life_payment(host, prepared)
    transitions = apply_life_changes(host, prepared.plan)
    return transitions[0]
