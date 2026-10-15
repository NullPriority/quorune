from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence
from .control_effects import ControlEffectHost, synchronize_control_effects

from .counter_removal import (
    commit_counter_removals,
    CounterRemoval,
    CounterRemovalError,
    CounterRemovalPlan,
    plan_counter_removals,
)
from .counter_state import (
    CounterStateError,
    CounterTransition,
    normalized_counter_name,
)
from .destruction import (
    commit_destruction_plan,
    DestructionCause,
    DestructionHost,
    DestructionPlan,
    prepare_destructions,
    request_for_card,
)
from .state_based_actions import PermanentSnapshot, StateBasedActionBatch, player_loss_seats
from .keyword_abilities import normalized_characteristic_keywords
from .ability_fragments import counter_maximum_values
from .saga_progression import saga_final_chapter_snapshot
from .zone_trigger_events import ZoneTransitionKind
from .util import unique_preserving_order


class StateBasedSnapshotHost(Protocol):
    state: Any
    active_seats: Sequence[str]

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...
    def _type_parts(self, type_line: str) -> tuple[set[str], set[str], set[str]]: ...
    def _numeric_stat(self, object_id: str, field: str) -> int: ...
    def _battle_trigger_pending(self, card: Any) -> bool: ...
    def _attachment_is_legal(self, card: Any, *, subtypes: set[str]) -> bool: ...


def permanent_sba_snapshots(host: StateBasedSnapshotHost) -> list[PermanentSnapshot]:
    """Capture one current public view before any state-based mutation."""
    snapshots: list[PermanentSnapshot] = []
    seen: set[str] = set()
    for seat in host.active_seats:
        for object_id in host.state.players[seat].zones["battlefield"]:
            if object_id in seen:
                continue
            seen.add(object_id)
            card = host.state.cards[object_id]
            if card.zone != "battlefield":
                continue
            if card.phased_out:
                snapshots.append(
                    PermanentSnapshot(
                        object_id=card.object_id,
                        deathtouch_damage=card.deathtouch_damage,
                        phased_out=True,
                    )
                )
                continue
            data = host._effective_card_data(card)
            card_types, subtypes, supertypes = host._type_parts(
                str(data.get("type_line") or "")
            )
            keywords = normalized_characteristic_keywords(data)
            snapshots.append(
                PermanentSnapshot(
                    object_id=card.object_id,
                    controller=card.controller,
                    timestamp=card.zone_timestamp,
                    card_types=frozenset(card_types),
                    subtypes=frozenset(subtypes),
                    world="world" in supertypes,
                    world_timestamp=(
                        card.world_supertype_timestamp
                    ),
                    toughness=(
                        host._numeric_stat(object_id, "toughness")
                        if "creature" in card_types
                        else None
                    ),
                    marked_damage=card.marked_damage,
                    deathtouch_damage=card.deathtouch_damage,
                    indestructible="indestructible" in keywords,
                    loyalty=(
                        int(card.counters.get("loyalty", 0))
                        if (
                            "planeswalker" in card_types
                            and (
                                "loyalty" in card.counters
                                or card.annotations.get(
                                    "loyalty_initialized"
                                )
                                or card.counters.get(
                                    "loyalty_initialized"
                                )
                            )
                        )
                        else None
                    ),
                    defense=(
                        int(card.counters.get("defense", 0))
                        if "battle" in card_types
                        else None
                    ),
                    battle_trigger_pending=(
                        host._battle_trigger_pending(card)
                        if "battle" in card_types
                        else False
                    ),
                    saga=saga_final_chapter_snapshot(host, card),
                    attached_to=card.attached_to,
                    attachment_legal=(
                        host._attachment_is_legal(
                            card,
                            subtypes=subtypes,
                        )
                        if card.attached_to is not None
                        else False
                    ),
                    counters=dict(card.counters),
                    counter_maximums=counter_maximum_values(
                        data.get("ability_fragments", ())
                    ),
                )
            )
    return snapshots


class StateBasedExecutionError(ValueError):
    """A state-based action batch cannot be prepared transactionally."""


class StateBasedPrincipalHost(ControlEffectHost, Protocol):
    def _eliminate_players(self, seats: list[str], *, reason: str) -> None: ...


def commit_state_based_principal_changes(host: StateBasedPrincipalHost) -> bool:
    """Settle layer-two custody before testing the current loss conditions."""
    if synchronize_control_effects(host, reason="control effect changed"):
        return True
    losers = player_loss_seats(host.state, host.active_seats)
    if not losers:
        from .rules.player_speed_state import start_player_speeds
        return start_player_speeds(host)
    host._eliminate_players(losers, reason="state-based loss")
    return True


@dataclass(frozen=True, slots=True)
class CounterPairRemoval:
    object_id: str
    pairs_removed: int


@dataclass(frozen=True, slots=True)
class CounterMaximumRemoval:
    object_id: str
    counter_name: str
    maximum: int
    required_removal: int


@dataclass(frozen=True, slots=True)
class StateBasedCounterRemovalPlan:
    counters: CounterRemovalPlan
    pairs: tuple[CounterPairRemoval, ...]
    maximums: tuple[CounterMaximumRemoval, ...]


@dataclass(frozen=True, slots=True)
class CounterMaximumRemovalResult:
    object_id: str
    counter_name: str
    before: int
    maximum: int
    required_removal: int
    after: int


@dataclass(frozen=True, slots=True)
class StateBasedCounterRemovalResult:
    pairs: tuple[CounterPairRemoval, ...]
    maximums: tuple[CounterMaximumRemovalResult, ...]


@dataclass(frozen=True, slots=True)
class StateBasedExecutionPlan:
    destruction: DestructionPlan
    ordinary_move_to_grave: tuple[str, ...]
    move_to_grave: tuple[str, ...]
    simultaneous_changes: tuple[tuple[str, str], ...]
    destruction_companions: tuple[tuple[str, str], ...]
    saga_sacrifices: tuple[str, ...]
    counter_removals: StateBasedCounterRemovalPlan
    state_changed: bool


def _prepare_counter_removals(
    host: DestructionHost,
    batch: StateBasedActionBatch,
    *,
    moving: frozenset[str],
) -> StateBasedCounterRemovalPlan:
    required: dict[tuple[str, str], int] = {}
    pairs: list[CounterPairRemoval] = []
    maximums: list[CounterMaximumRemoval] = []
    pair_objects: set[str] = set()
    maximum_keys: set[tuple[str, str]] = set()

    def surviving_card(object_id: str):
        if type(object_id) is not str or not object_id:
            raise StateBasedExecutionError(
                "State-based counter removal requires object identity"
            )
        card = host.state.cards.get(object_id)
        if card is None:
            raise StateBasedExecutionError(
                "State-based counter removal names an unknown object"
            )
        if object_id in moving or card.zone != "battlefield":
            return None
        return card

    for object_id, amount in batch.counter_pairs_to_remove:
        card = surviving_card(object_id)
        if card is None:
            continue
        if object_id in pair_objects:
            raise StateBasedExecutionError(
                "Opposing-counter removal names an object twice"
            )
        pair_objects.add(object_id)
        if type(amount) is not int or amount <= 0:
            raise StateBasedExecutionError(
                "Opposing-counter removal must be a positive integer"
            )
        pairs.append(CounterPairRemoval(object_id, amount))
        for counter_name in ("+1/+1", "-1/-1"):
            key = (object_id, counter_name)
            required[key] = max(required.get(key, 0), amount)

    for object_id, counter_name, amount in (
        batch.counter_maximums_to_remove
    ):
        card = surviving_card(object_id)
        if card is None:
            continue
        if type(counter_name) is not str:
            raise StateBasedExecutionError(
                "Maximum-counter removal requires a counter name"
            )
        try:
            counter_name = normalized_counter_name(counter_name)
        except CounterStateError as exc:
            raise StateBasedExecutionError(str(exc)) from exc
        maximum_key = (object_id, counter_name)
        if maximum_key in maximum_keys:
            raise StateBasedExecutionError(
                "Maximum-counter removal names a counter twice"
            )
        maximum_keys.add(maximum_key)
        if type(amount) is not int or amount <= 0:
            raise StateBasedExecutionError(
                "Maximum-counter removal must be a positive integer"
            )
        before_value = card.counters.get(counter_name, 0)
        if type(before_value) is not int or before_value < amount:
            raise StateBasedExecutionError(
                "Maximum-counter snapshot is malformed"
            )
        before = before_value
        maximums.append(
            CounterMaximumRemoval(
                object_id=object_id,
                counter_name=counter_name,
                maximum=before - amount,
                required_removal=amount,
            )
        )
        key = (object_id, counter_name)
        # Simultaneous SBA instructions may name the same indistinguishable
        # counters. The greatest required removal satisfies both instructions.
        required[key] = max(required.get(key, 0), amount)

    removals = tuple(
        CounterRemoval(
            object_id=object_id,
            counter_name=counter_name,
            amount=amount,
            expected_logical_object_id=(
                host.state.cards[object_id].logical_object_id
            ),
        )
        for (object_id, counter_name), amount in sorted(required.items())
    )
    try:
        counter_plan = plan_counter_removals(host, removals)
    except CounterRemovalError as exc:
        raise StateBasedExecutionError(str(exc)) from exc
    return StateBasedCounterRemovalPlan(
        counters=counter_plan,
        pairs=tuple(
            sorted(pairs, key=lambda value: value.object_id)
        ),
        maximums=tuple(
            sorted(
                maximums,
                key=lambda value: (
                    value.object_id,
                    value.counter_name,
                ),
            )
        ),
    )


def prepare_state_based_execution(
    host: DestructionHost,
    batch: StateBasedActionBatch,
    *, replacement_selections: Sequence[Any] = (),
) -> StateBasedExecutionPlan:
    """Bind one pure SBA snapshot to typed destruction and zone owners."""

    if not isinstance(batch, StateBasedActionBatch):
        raise StateBasedExecutionError(
            "State-based execution requires a typed action batch"
        )
    destruction = prepare_destructions(
        host,
        tuple(
            request_for_card(host.state.cards[object_id])
            for object_id in batch.destroy
        ),
        cause=DestructionCause.STATE_BASED_ACTION,
        actor=None,
        reason="state-based action",
        replacement_selections=replacement_selections,
    )
    saga_ids = tuple(
        value.object_id for value in batch.saga_sacrifices
    )
    for value in batch.saga_sacrifices:
        card = host.state.cards.get(value.object_id)
        if (
            card is None
            or card.zone != "battlefield"
            or card.logical_object_id != value.logical_object_id
            or card.controller != value.controller
        ):
            raise StateBasedExecutionError(
                "Saga lifecycle snapshot changed before commit"
            )
    ordinary = tuple(
        unique_preserving_order(
            (*batch.put_in_graveyard, *destruction.destroyed_object_ids)
        )
    )
    moved = tuple(
        unique_preserving_order(
            (*ordinary, *batch.world_rule, *saga_ids)
        )
    )
    if any(
        not isinstance(object_id, str)
        or not object_id
        or object_id not in host.state.cards
        for object_id in moved
    ):
        raise StateBasedExecutionError(
            "State-based zone changes contain an unknown object"
        )
    simultaneous = tuple(
        (object_id, "graveyard")
        for object_id in moved
        if host.state.cards[object_id].zone == "battlefield"
    )
    destroyed = set(destruction.destroyed_object_ids)
    companions = tuple(
        change for change in simultaneous if change[0] not in destroyed
    )
    counter_removals = _prepare_counter_removals(
        host,
        batch,
        moving=frozenset(moved),
    )
    return StateBasedExecutionPlan(
        destruction=destruction,
        ordinary_move_to_grave=ordinary,
        move_to_grave=moved,
        simultaneous_changes=simultaneous,
        destruction_companions=companions,
        saga_sacrifices=saga_ids,
        counter_removals=counter_removals,
        state_changed=bool(
            ordinary
            or batch.detach
            or counter_removals.counters.removals
            or batch.cease
            or batch.world_rule
            or saga_ids
        ),
    )


def commit_state_based_zone_changes(
    host: DestructionHost,
    plan: StateBasedExecutionPlan,
) -> None:
    from .conditional_exile_return import defer_conditional_cost_returns, finish_conditional_cost_returns
    with defer_conditional_cost_returns() as waiting:
        _commit_state_based_zone_changes(host,plan)
    finish_conditional_cost_returns(host,waiting)


def _commit_state_based_zone_changes(
    host: DestructionHost,
    plan: StateBasedExecutionPlan,
) -> None:
    if not isinstance(plan, StateBasedExecutionPlan):
        raise StateBasedExecutionError(
            "State-based commit requires a typed execution plan"
        )
    if not plan.simultaneous_changes:
        return
    commit_destruction_plan(
        host,
        plan.destruction,
        companion_changes=plan.destruction_companions,
        companion_transition_kinds={
            object_id: ZoneTransitionKind.SACRIFICE
            for object_id in plan.saga_sacrifices
        },
    )
    log_saga_sacrifices(host, plan.saga_sacrifices)


def log_saga_sacrifices(
    host: DestructionHost,
    object_ids: tuple[str, ...],
) -> None:
    """Journal one committed Saga SBA batch without owning its mutation."""

    if not object_ids:
        return
    cards = tuple(host.state.cards[object_id] for object_id in object_ids)
    host._log(
        None,
        "state.saga_sacrificed",
        f"State-based actions sacrificed {len(cards)} completed Saga permanent(s).",
        {"objects": [card.ref for card in cards], "rule": "704.5s"},
        importance=2,
        changed_objects=object_ids,
        changed_players=sorted({card.owner for card in cards}),
    )


def commit_state_based_counter_removals(
    host: DestructionHost,
    plan: StateBasedCounterRemovalPlan,
) -> StateBasedCounterRemovalResult:
    """Commit the surviving portion of one simultaneous SBA snapshot."""

    if not isinstance(plan, StateBasedCounterRemovalPlan):
        raise StateBasedExecutionError(
            "State-based counter commit requires a typed plan"
        )
    try:
        transitions = commit_counter_removals(host, plan.counters)
    except CounterRemovalError as exc:
        raise StateBasedExecutionError(str(exc)) from exc
    by_key: dict[tuple[str, str], CounterTransition] = {
        (transition.subject_id, transition.counter_name): transition
        for transition in transitions
    }
    maximum_results = tuple(
        CounterMaximumRemovalResult(
            object_id=value.object_id,
            counter_name=value.counter_name,
            before=by_key[(value.object_id, value.counter_name)].before,
            maximum=value.maximum,
            required_removal=value.required_removal,
            after=by_key[(value.object_id, value.counter_name)].after,
        )
        for value in plan.maximums
    )
    return StateBasedCounterRemovalResult(
        pairs=plan.pairs,
        maximums=maximum_results,
    )


__all__ = [
    "permanent_sba_snapshots",
    "StateBasedSnapshotHost",
    "commit_state_based_counter_removals",
    "commit_state_based_zone_changes",
    "CounterMaximumRemoval",
    "CounterMaximumRemovalResult",
    "CounterPairRemoval",
    "prepare_state_based_execution",
    "log_saga_sacrifices",
    "StateBasedCounterRemovalPlan",
    "StateBasedCounterRemovalResult",
    "StateBasedExecutionError",
    "StateBasedExecutionPlan",
]
