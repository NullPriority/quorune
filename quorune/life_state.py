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


def _record_life_change(
    host: LifeStateHost,
    transitions: Sequence["LifeTransition"],
) -> None:
    from .rules.player_speed_state import queue_speed_loss_trigger
    queue_speed_loss_trigger(host,transitions)
    recorder = getattr(host, "_record_turn_history", None)
    if not callable(recorder):
        return
    for transition in transitions:
        lost = transition.before - transition.after
        gained = transition.after - transition.before
        if lost > 0:
            recorder(
                "player_lost_life",
                target=transition.player,
                target_kind="player",
                amount=lost,
            )
        elif gained > 0:
            recorder(
                "player_gained_life",
                target=transition.player,
                target_kind="player",
                amount=gained,
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
    _record_life_change(host, transitions)
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
        _record_life_change(host, transitions)
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


def available_life_for_payment(host: LifeStateHost, player: str) -> int:
    """Read the current limit used to price nonreplaceable life payments."""

    current = _current_life(host, player)
    if not current:
        return 0
    from .semantic_runtime.life_prohibitions import life_payment_prohibited

    return 0 if life_payment_prohibited(host, player) else current


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
    # CR 119.4b permits zero life even when loss is prohibited.
    if amount > 0:
        if _current_life(host, player) < amount:
            raise LifeStateError("Cannot pay more life than the player has")
        from .semantic_runtime.life_prohibitions import life_payment_prohibited

        if life_payment_prohibited(host, player):
            raise LifeStateError("This player cannot pay a positive amount of life")
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
        or (prepared.amount > 0 and transition.before < prepared.amount)
    ):
        raise LifeStateError("Life payment plan does not match its request")
    if prepared.amount > 0:
        from .semantic_runtime.life_prohibitions import life_payment_prohibited

        if life_payment_prohibited(host, prepared.player):
            raise LifeStateError("Life payment is no longer permitted")
    validate_life_changes(host, prepared.plan)


def dispatch_life_payment_loss(host: LifeStateHost, payment: LifeTransition, *, first_loss_player: str | None = None) -> None:
    from .player_result_events import LifeLossEvent, dispatch_life_loss_events

    amount = payment.before - payment.after
    if amount > 0:
        event = LifeLossEvent(payment.player, amount, first_own_turn_loss=payment.player == first_loss_player)
        dispatch_life_loss_events(host, (event,))


def commit_life_payment(
    host: LifeStateHost,
    prepared: PreparedLifePayment,
) -> LifeTransition:
    """Commit a replay-validated nonreplaceable life payment."""

    from .rules.first_own_turn_life_loss import first_own_turn_loss_player_before_commit

    validate_life_payment(host, prepared)
    first_loss_player = first_own_turn_loss_player_before_commit(host)
    payment, = apply_life_changes(host, prepared.plan)
    dispatch_life_payment_loss(host, payment, first_loss_player=first_loss_player)
    return payment
