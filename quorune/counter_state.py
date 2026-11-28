from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence

from .counter_names import CounterStateError, normalized_counter_name
from .model import PLAYER_COUNTERS_FIELD
from .state_planner import (
    apply_state_plan,
    commit_state_plan,
    plan_state_changes,
    validate_state_plan,
)


CounterSubjectKind = Literal["player", "permanent"]
_PLAYER_COUNTER_ATTRIBUTES = {
    "energy": "energy",
    "poison": "poison",
}


class CounterStateHost(Protocol):
    state: Any


def player_counter_snapshot(player: Any) -> dict[str, int]:
    """Return every positive public counter on one player canonically."""

    result = {
        name: int(getattr(player, attribute))
        for name, attribute in sorted(_PLAYER_COUNTER_ATTRIBUTES.items())
        if int(getattr(player, attribute)) > 0
    }
    generic = getattr(player, PLAYER_COUNTERS_FIELD, {})
    if not isinstance(generic, Mapping):
        raise CounterStateError("Player counter state must be a mapping")
    for raw_name, raw_amount in generic.items():
        name = normalized_counter_name(str(raw_name))
        if name in _PLAYER_COUNTER_ATTRIBUTES:
            raise CounterStateError(
                "Poison and energy cannot be duplicated in generic counters"
            )
        if type(raw_amount) is not int or raw_amount < 0:
            raise CounterStateError(
                "Player counter amounts must be nonnegative integers"
            )
        if raw_amount:
            if name in result:
                raise CounterStateError(
                    "Player counter names must remain unique after normalization"
                )
            result[name] = raw_amount
    return dict(sorted(result.items()))


@dataclass(frozen=True, slots=True)
class CounterChange:
    subject_kind: CounterSubjectKind
    subject_id: str
    counter_name: str
    amount: int
    expected_zone: str | None = None
    expected_logical_object_id: str | None = None

    def __post_init__(self) -> None:
        if self.subject_kind not in {"player", "permanent"}:
            raise CounterStateError(
                "Counter subjects must be players or permanents"
            )
        if not self.subject_id:
            raise CounterStateError("Counter changes require a subject ID")
        object.__setattr__(
            self,
            "counter_name",
            normalized_counter_name(self.counter_name),
        )
        if type(self.amount) is not int:
            raise CounterStateError("Counter change amounts must be integers")
        if self.subject_kind == "player" and (
            self.expected_zone is not None
            or self.expected_logical_object_id is not None
        ):
            raise CounterStateError(
                "Player counters cannot carry permanent identity constraints"
            )


@dataclass(frozen=True, slots=True)
class CounterTransition:
    subject_kind: CounterSubjectKind
    subject_id: str
    counter_name: str
    requested_delta: int
    applied_delta: int
    before: int
    after: int
    expected_zone: str | None = None
    expected_logical_object_id: str | None = None


@dataclass(frozen=True, slots=True)
class CounterStatePlan:
    transitions: tuple[CounterTransition, ...]

    @property
    def changed_players(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    transition.subject_id
                    for transition in self.transitions
                    if transition.subject_kind == "player"
                    and transition.applied_delta
                }
            )
        )

    @property
    def changed_objects(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    transition.subject_id
                    for transition in self.transitions
                    if transition.subject_kind == "permanent"
                    and transition.applied_delta
                }
            )
        )


def _player_counter_value(host: CounterStateHost, seat: str, name: str) -> int:
    player = host.state.players.get(seat)
    if player is None:
        raise CounterStateError("Counter-change player does not exist")
    attribute = _PLAYER_COUNTER_ATTRIBUTES.get(name)
    if attribute is not None:
        value = getattr(player, attribute)
    else:
        if not isinstance(player.counters, Mapping):
            raise CounterStateError("Player counter state must be a mapping")
        value = player.counters.get(name, 0)
    if type(value) is not int or value < 0:
        raise CounterStateError(
            "Player counters must be nonnegative integers"
        )
    return value


def _permanent_counter_value(
    host: CounterStateHost,
    change: CounterChange,
) -> int:
    card = host.state.cards.get(change.subject_id)
    if card is None:
        raise CounterStateError("Counter-change permanent does not exist")
    if change.expected_zone is not None and card.zone != change.expected_zone:
        raise CounterStateError(
            "Counter-change permanent changed zones before commit"
        )
    if (
        change.expected_logical_object_id is not None
        and card.logical_object_id != change.expected_logical_object_id
    ):
        raise CounterStateError(
            "Counter-change permanent changed object identity"
        )
    if not isinstance(card.counters, Mapping):
        raise CounterStateError("Permanent counter state must be a mapping")
    value = card.counters.get(change.counter_name, 0)
    if type(value) is not int or value < 0:
        raise CounterStateError(
            "Permanent counters must be nonnegative integers"
        )
    return value


def _current_value(host: CounterStateHost, change: CounterChange) -> int:
    if change.subject_kind == "player":
        return _player_counter_value(
            host, change.subject_id, change.counter_name
        )
    return _permanent_counter_value(host, change)


class _CounterAdapter:
    @staticmethod
    def validate_change(change: CounterChange) -> None:
        if not isinstance(change, CounterChange):
            raise CounterStateError("Counter plans require typed changes")

    @staticmethod
    def key(change: CounterChange) -> tuple[str, str, str]:
        return change.subject_kind, change.subject_id, change.counter_name

    @staticmethod
    def current_value(host: CounterStateHost, change: CounterChange) -> int:
        return _current_value(host, change)

    @staticmethod
    def next_value(before: int, change: CounterChange) -> int:
        return max(0, before + change.amount)

    @staticmethod
    def transition(
        change: CounterChange, *, before: int, after: int
    ) -> CounterTransition:
        return CounterTransition(
            subject_kind=change.subject_kind,
            subject_id=change.subject_id,
            counter_name=change.counter_name,
            requested_delta=change.amount,
            applied_delta=after - before,
            before=before,
            after=after,
            expected_zone=change.expected_zone,
            expected_logical_object_id=change.expected_logical_object_id,
        )

    @staticmethod
    def change_from_transition(transition: CounterTransition) -> CounterChange:
        return _transition_as_change(transition)

    @staticmethod
    def transition_before(transition: CounterTransition) -> int:
        return transition.before

    @staticmethod
    def transition_after(transition: CounterTransition) -> int:
        return transition.after

    @staticmethod
    def validate_transition(transition: CounterTransition) -> None:
        if not isinstance(transition, CounterTransition):
            raise CounterStateError("Counter commits require typed transitions")
        expected_after = max(0, transition.before + transition.requested_delta)
        if transition.after != expected_after:
            raise CounterStateError("Counter transition arithmetic is invalid")
        if transition.applied_delta != transition.after - transition.before:
            raise CounterStateError("Counter applied delta is invalid")

    @staticmethod
    def apply_final(
        host: CounterStateHost, transition: CounterTransition
    ) -> None:
        if transition.subject_kind == "player":
            player = host.state.players[transition.subject_id]
            attribute = _PLAYER_COUNTER_ATTRIBUTES.get(
                transition.counter_name
            )
            if attribute is not None:
                setattr(player, attribute, transition.after)
            elif transition.after:
                player.counters[transition.counter_name] = transition.after
            else:
                player.counters.pop(transition.counter_name, None)
            return
        card = host.state.cards[transition.subject_id]
        if transition.after:
            card.counters[transition.counter_name] = transition.after
        else:
            card.counters.pop(transition.counter_name, None)
            if transition.before:
                from .continuous_effect_state import expire_counter_bound_attack_restrictions

                expire_counter_bound_attack_restrictions(host.state, card, transition.counter_name)


_COUNTER_ADAPTER = _CounterAdapter()


def _counter_planner_error(error: ValueError) -> CounterStateError:
    if str(error) == "State plan is stale":
        return CounterStateError("Counter plan is stale")
    if str(error) == "State plan changed before commit":
        return CounterStateError("Counter plan changed before commit")
    return CounterStateError(str(error))


def plan_counter_changes(
    host: CounterStateHost,
    changes: Sequence[CounterChange],
) -> CounterStatePlan:
    """Validate a batch and compute every transition without mutation."""

    return CounterStatePlan(
        plan_state_changes(host, changes, _COUNTER_ADAPTER)
    )


def _transition_as_change(transition: CounterTransition) -> CounterChange:
    return CounterChange(
        subject_kind=transition.subject_kind,
        subject_id=transition.subject_id,
        counter_name=transition.counter_name,
        amount=transition.requested_delta,
        expected_zone=transition.expected_zone,
        expected_logical_object_id=transition.expected_logical_object_id,
    )


def validate_counter_changes(
    host: CounterStateHost,
    plan: CounterStatePlan,
) -> None:
    """Fail before mutation if any transition or identity is stale."""

    if not isinstance(plan, CounterStatePlan):
        raise CounterStateError("Counter commits require a typed plan")
    try:
        validate_state_plan(host, plan.transitions, _COUNTER_ADAPTER)
    except ValueError as exc:
        if isinstance(exc, CounterStateError):
            raise
        raise _counter_planner_error(exc) from exc


def apply_counter_changes(
    host: CounterStateHost,
    plan: CounterStatePlan,
) -> tuple[CounterTransition, ...]:
    """Apply a counter plan after the caller completed precommit validation."""

    return apply_state_plan(host, plan.transitions, _COUNTER_ADAPTER)


def commit_counter_changes(
    host: CounterStateHost,
    plan: CounterStatePlan,
) -> tuple[CounterTransition, ...]:
    """Validate and commit one typed counter batch."""

    if not isinstance(plan, CounterStatePlan):
        raise CounterStateError("Counter commits require a typed plan")
    try:
        return commit_state_plan(host, plan.transitions, _COUNTER_ADAPTER)
    except ValueError as exc:
        if isinstance(exc, CounterStateError):
            raise
        raise _counter_planner_error(exc) from exc
