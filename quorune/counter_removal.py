from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .counter_state import (
    commit_counter_changes,
    CounterChange,
    CounterStateError,
    CounterStateHost,
    CounterStatePlan,
    CounterTransition,
    normalized_counter_name,
    plan_counter_changes,
    validate_counter_changes,
)


class CounterRemovalError(ValueError):
    """A mandatory counter-removal transaction is malformed or stale."""


class CounterRemovalHost(CounterStateHost, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class CounterRemoval:
    object_id: str
    counter_name: str
    amount: int
    expected_zone: str = "battlefield"
    expected_logical_object_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.object_id) is not str or not self.object_id:
            raise CounterRemovalError(
                "Counter removal requires permanent identity"
            )
        if type(self.counter_name) is not str:
            raise CounterRemovalError(
                "Counter removal requires a counter name"
            )
        try:
            normalized_name = normalized_counter_name(self.counter_name)
        except CounterStateError as exc:
            raise CounterRemovalError(str(exc)) from exc
        object.__setattr__(self, "counter_name", normalized_name)
        if type(self.amount) is not int or self.amount <= 0:
            raise CounterRemovalError(
                "Counter removal amounts must be positive integers"
            )
        if type(self.expected_zone) is not str or not self.expected_zone:
            raise CounterRemovalError(
                "Counter removal requires an expected zone"
            )
        if self.expected_logical_object_id is not None and (
            type(self.expected_logical_object_id) is not str
            or not self.expected_logical_object_id
        ):
            raise CounterRemovalError(
                "Counter removal logical identity must be a nonempty string"
            )

    @property
    def key(self) -> tuple[str, str]:
        return self.object_id, self.counter_name


@dataclass(frozen=True, slots=True)
class CounterRemovalPlan:
    removals: tuple[CounterRemoval, ...]
    counter_plan: CounterStatePlan

    def __post_init__(self) -> None:
        if not isinstance(self.counter_plan, CounterStatePlan):
            raise CounterRemovalError(
                "Counter-removal plan requires a typed counter plan"
            )
        if len(self.removals) != len(self.counter_plan.transitions):
            raise CounterRemovalError(
                "Counter-removal plan shape is inconsistent"
            )


@dataclass(frozen=True, slots=True)
class CounterRemovalResult:
    """The exact result of one fixed removal effect.

    Effects remove as many counters as possible up to the requested amount.
    Costs and rule requirements continue to use :class:`CounterRemovalPlan`,
    whose removal is exact and fails when the full amount is unavailable.
    """

    object_id: str
    counter_name: str
    requested: int
    removed: int
    before: int
    after: int

    def __post_init__(self) -> None:
        if type(self.object_id) is not str or not self.object_id:
            raise CounterRemovalError(
                "Counter-removal results require permanent identity"
            )
        try:
            normalized_name = normalized_counter_name(self.counter_name)
        except CounterStateError as exc:
            raise CounterRemovalError(str(exc)) from exc
        object.__setattr__(self, "counter_name", normalized_name)
        if (
            type(self.requested) is not int
            or self.requested <= 0
            or type(self.removed) is not int
            or not 0 <= self.removed <= self.requested
            or type(self.before) is not int
            or type(self.after) is not int
            or self.before < 0
            or self.after < 0
            or self.before - self.after != self.removed
        ):
            raise CounterRemovalError(
                "Counter-removal result arithmetic is invalid"
            )


@dataclass(frozen=True, slots=True)
class CounterRemovalEffectPlan:
    """One preflighted fixed removal with partial-effect semantics."""

    removal: CounterRemoval
    counter_plan: CounterStatePlan

    def __post_init__(self) -> None:
        if not isinstance(self.removal, CounterRemoval):
            raise CounterRemovalError(
                "Counter-removal effects require one typed removal"
            )
        if (
            not isinstance(self.counter_plan, CounterStatePlan)
            or len(self.counter_plan.transitions) != 1
        ):
            raise CounterRemovalError(
                "Counter-removal effects require one typed transition"
            )
        _validate_partial_removal(
            self.removal,
            self.counter_plan.transitions[0],
        )


@dataclass(frozen=True, slots=True)
class AllCounterRemovalEffectPlan:
    """One identity-pinned removal of every counter on one permanent."""

    object_id: str
    expected_zone: str
    expected_logical_object_id: str
    removal_plan: CounterRemovalPlan

    def __post_init__(self) -> None:
        if type(self.object_id) is not str or not self.object_id:
            raise CounterRemovalError(
                "All-counter removal requires permanent identity"
            )
        if type(self.expected_zone) is not str or not self.expected_zone:
            raise CounterRemovalError(
                "All-counter removal requires an expected zone"
            )
        if (
            type(self.expected_logical_object_id) is not str
            or not self.expected_logical_object_id
        ):
            raise CounterRemovalError(
                "All-counter removal requires logical identity"
            )
        if not isinstance(self.removal_plan, CounterRemovalPlan):
            raise CounterRemovalError(
                "All-counter removal requires one typed removal plan"
            )
        if any(
            removal.object_id != self.object_id
            or removal.expected_zone != self.expected_zone
            or removal.expected_logical_object_id
            != self.expected_logical_object_id
            for removal in self.removal_plan.removals
        ):
            raise CounterRemovalError(
                "All-counter removal plan targets are inconsistent"
            )


@dataclass(frozen=True, slots=True)
class AllCounterRemovalResult:
    object_id: str
    removed: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if type(self.object_id) is not str or not self.object_id:
            raise CounterRemovalError(
                "All-counter removal results require permanent identity"
            )
        if type(self.removed) is not tuple:
            raise CounterRemovalError(
                "All-counter removal results must be canonical"
            )
        names: list[str] = []
        for entry in self.removed:
            if type(entry) is not tuple or len(entry) != 2:
                raise CounterRemovalError(
                    "All-counter removal results must be canonical"
                )
            name, amount = entry
            if type(name) is not str or type(amount) is not int or amount <= 0:
                raise CounterRemovalError(
                    "All-counter removal results must be canonical"
                )
            try:
                normalized_name = normalized_counter_name(name)
            except CounterStateError as exc:
                raise CounterRemovalError(str(exc)) from exc
            if name != normalized_name:
                raise CounterRemovalError(
                    "All-counter removal results must be canonical"
                )
            names.append(name)
        if names != sorted(names) or len(names) != len(set(names)):
            raise CounterRemovalError(
                "All-counter removal results must be canonical"
            )

    @property
    def total_removed(self) -> int:
        return sum(amount for _, amount in self.removed)


def _counter_change(removal: CounterRemoval) -> CounterChange:
    return CounterChange(
        subject_kind="permanent",
        subject_id=removal.object_id,
        counter_name=removal.counter_name,
        amount=-removal.amount,
        expected_zone=removal.expected_zone,
        expected_logical_object_id=removal.expected_logical_object_id,
    )


def _validate_exact_removal(
    removal: CounterRemoval,
    transition: CounterTransition,
) -> None:
    if (
        transition.subject_kind != "permanent"
        or transition.subject_id != removal.object_id
        or transition.counter_name != removal.counter_name
        or transition.requested_delta != -removal.amount
        or transition.applied_delta != -removal.amount
    ):
        raise CounterRemovalError(
            "The permanent does not have enough counters to remove"
        )


def _validate_partial_removal(
    removal: CounterRemoval,
    transition: CounterTransition,
) -> None:
    if (
        transition.subject_kind != "permanent"
        or transition.subject_id != removal.object_id
        or transition.counter_name != removal.counter_name
        or transition.requested_delta != -removal.amount
        or transition.applied_delta > 0
        or -transition.applied_delta > removal.amount
        or transition.expected_zone != removal.expected_zone
        or transition.expected_logical_object_id
        != removal.expected_logical_object_id
    ):
        raise CounterRemovalError(
            "Counter-removal effect transition is inconsistent"
        )


def _partial_result(
    removal: CounterRemoval,
    transition: CounterTransition,
) -> CounterRemovalResult:
    _validate_partial_removal(removal, transition)
    return CounterRemovalResult(
        object_id=removal.object_id,
        counter_name=removal.counter_name,
        requested=removal.amount,
        removed=-transition.applied_delta,
        before=transition.before,
        after=transition.after,
    )


def plan_counter_removals(
    host: CounterRemovalHost,
    removals: Sequence[CounterRemoval],
) -> CounterRemovalPlan:
    """Canonicalize and preflight an exact permanent-counter removal batch."""

    supplied = tuple(removals)
    if any(not isinstance(removal, CounterRemoval) for removal in supplied):
        raise CounterRemovalError(
            "Counter-removal plans require typed removals"
        )
    canonical = tuple(sorted(supplied, key=lambda value: value.key))
    keys = tuple(removal.key for removal in canonical)
    if len(keys) != len(set(keys)):
        raise CounterRemovalError(
            "Counter-removal plans require one request per counter kind"
        )
    try:
        counter_plan = plan_counter_changes(
            host,
            tuple(_counter_change(removal) for removal in canonical),
        )
    except CounterStateError as exc:
        raise CounterRemovalError(str(exc)) from exc
    for removal, transition in zip(
        canonical,
        counter_plan.transitions,
        strict=True,
    ):
        _validate_exact_removal(removal, transition)
    return CounterRemovalPlan(canonical, counter_plan)


def plan_counter_removal_effect(
    host: CounterRemovalHost,
    removal: CounterRemoval,
) -> CounterRemovalEffectPlan:
    """Preflight one fixed removal that does as much as possible."""

    if not isinstance(removal, CounterRemoval):
        raise CounterRemovalError(
            "Counter-removal effects require one typed removal"
        )
    try:
        counter_plan = plan_counter_changes(host, (_counter_change(removal),))
    except CounterStateError as exc:
        raise CounterRemovalError(str(exc)) from exc
    return CounterRemovalEffectPlan(removal, counter_plan)


def _permanent_counter_snapshot(
    host: CounterRemovalHost,
    *,
    object_id: str,
    expected_zone: str,
    expected_logical_object_id: str,
) -> tuple[tuple[str, int], ...]:
    card = host.state.cards.get(object_id)
    if card is None:
        raise CounterRemovalError(
            "Counter-removal permanent does not exist"
        )
    if (
        card.zone != expected_zone
        or card.logical_object_id != expected_logical_object_id
    ):
        raise CounterRemovalError(
            "Counter-removal permanent changed identity or zone"
        )
    raw_counters = card.counters
    if not isinstance(raw_counters, Mapping):
        raise CounterRemovalError(
            "Permanent counter state must be a mapping"
        )
    counters: dict[str, int] = {}
    for raw_name, raw_amount in raw_counters.items():
        try:
            name = normalized_counter_name(raw_name)
        except CounterStateError as exc:
            raise CounterRemovalError(str(exc)) from exc
        if name in counters:
            raise CounterRemovalError(
                "Permanent counter names must remain unique after normalization"
            )
        if type(raw_amount) is not int or raw_amount < 0:
            raise CounterRemovalError(
                "Permanent counter amounts must be nonnegative integers"
            )
        if raw_amount:
            counters[name] = raw_amount
    return tuple(sorted(counters.items()))


def plan_all_counter_removal_effect(
    host: CounterRemovalHost,
    *,
    object_id: str,
    expected_zone: str = "battlefield",
    expected_logical_object_id: str,
) -> AllCounterRemovalEffectPlan:
    """Snapshot and preflight every positive counter on one permanent."""

    counters = _permanent_counter_snapshot(
        host,
        object_id=object_id,
        expected_zone=expected_zone,
        expected_logical_object_id=expected_logical_object_id,
    )
    removal_plan = plan_counter_removals(
        host,
        tuple(
            CounterRemoval(
                object_id=object_id,
                counter_name=name,
                amount=amount,
                expected_zone=expected_zone,
                expected_logical_object_id=expected_logical_object_id,
            )
            for name, amount in counters
        ),
    )
    return AllCounterRemovalEffectPlan(
        object_id=object_id,
        expected_zone=expected_zone,
        expected_logical_object_id=expected_logical_object_id,
        removal_plan=removal_plan,
    )


def _commit_removal_counter_plan(
    host: CounterRemovalHost, plan: CounterStatePlan,
) -> tuple[CounterTransition, ...]:
    from .counter_removal_events import capture_counter_removal_events, dispatch_counter_removal_events
    try:
        transitions = commit_counter_changes(host, plan)
    except CounterStateError as exc:
        raise CounterRemovalError(str(exc)) from exc
    events = capture_counter_removal_events(host, transitions)
    dispatch_counter_removal_events(host, events)
    return transitions


def commit_counter_removals(
    host: CounterRemovalHost,
    plan: CounterRemovalPlan,
) -> tuple[CounterTransition, ...]:
    """Commit an exact preflighted removal batch through counter state."""

    validate_counter_removal_plan(host, plan)
    return _commit_removal_counter_plan(host, plan.counter_plan)


def validate_counter_removal_plan(
    host: CounterRemovalHost,
    plan: CounterRemovalPlan,
) -> None:
    """Fail before mutation when an exact removal plan became stale."""

    if not isinstance(plan, CounterRemovalPlan):
        raise CounterRemovalError(
            "Counter-removal validation requires a typed plan"
        )
    for removal, transition in zip(
        plan.removals,
        plan.counter_plan.transitions,
        strict=True,
    ):
        _validate_exact_removal(removal, transition)
    try:
        validate_counter_changes(host, plan.counter_plan)
    except CounterStateError as exc:
        raise CounterRemovalError(str(exc)) from exc


def validate_counter_removal_effect_plan(
    host: CounterRemovalHost,
    plan: CounterRemovalEffectPlan,
) -> None:
    """Fail before mutation when a fixed removal effect became stale."""

    if not isinstance(plan, CounterRemovalEffectPlan):
        raise CounterRemovalError(
            "Counter-removal effect validation requires a typed plan"
        )
    _validate_partial_removal(plan.removal, plan.counter_plan.transitions[0])
    try:
        validate_counter_changes(host, plan.counter_plan)
    except CounterStateError as exc:
        raise CounterRemovalError(str(exc)) from exc


def commit_counter_removal_effect(
    host: CounterRemovalHost,
    plan: CounterRemovalEffectPlan,
) -> CounterRemovalResult:
    """Commit one preflighted fixed removal through counter state."""

    validate_counter_removal_effect_plan(host, plan)
    transitions = _commit_removal_counter_plan(host, plan.counter_plan)
    if len(transitions) != 1:
        raise CounterRemovalError(
            "Counter-removal effect committed an invalid transition shape"
        )
    return _partial_result(plan.removal, transitions[0])


def validate_all_counter_removal_effect_plan(
    host: CounterRemovalHost,
    plan: AllCounterRemovalEffectPlan,
) -> None:
    if not isinstance(plan, AllCounterRemovalEffectPlan):
        raise CounterRemovalError(
            "All-counter removal validation requires a typed plan"
        )
    current = _permanent_counter_snapshot(
        host,
        object_id=plan.object_id,
        expected_zone=plan.expected_zone,
        expected_logical_object_id=plan.expected_logical_object_id,
    )
    planned = tuple(
        (removal.counter_name, removal.amount)
        for removal in plan.removal_plan.removals
    )
    if current != planned:
        raise CounterRemovalError(
            "All-counter removal plan became stale"
        )
    validate_counter_removal_plan(host, plan.removal_plan)


def commit_all_counter_removal_effect(
    host: CounterRemovalHost,
    plan: AllCounterRemovalEffectPlan,
) -> AllCounterRemovalResult:
    validate_all_counter_removal_effect_plan(host, plan)
    transitions = commit_counter_removals(host, plan.removal_plan)
    return AllCounterRemovalResult(
        object_id=plan.object_id,
        removed=tuple(
            (transition.counter_name, -transition.applied_delta)
            for transition in transitions
            if transition.applied_delta
        ),
    )


__all__ = [
    "AllCounterRemovalEffectPlan",
    "AllCounterRemovalResult",
    "commit_all_counter_removal_effect",
    "commit_counter_removal_effect",
    "commit_counter_removals",
    "CounterRemoval",
    "CounterRemovalEffectPlan",
    "CounterRemovalError",
    "CounterRemovalHost",
    "CounterRemovalPlan",
    "CounterRemovalResult",
    "plan_all_counter_removal_effect",
    "plan_counter_removal_effect",
    "plan_counter_removals",
    "validate_all_counter_removal_effect_plan",
    "validate_counter_removal_effect_plan",
    "validate_counter_removal_plan",
]
