from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence
from .counter_placement_event_model import CounterPlacementOccurrence

from . import deathtouch as deathtouch_rules
from .counter_placement import (
    commit_counter_placement_plan,
    CounterPlacementCommitPlan,
    CounterPlacementError,
    plan_resolved_counter_placement_commit,
    validate_counter_placement_commit,
)
from .counter_removal import (
    commit_counter_removals,
    CounterRemoval,
    CounterRemovalError,
    CounterRemovalPlan,
    plan_counter_removals,
    validate_counter_removal_plan,
)
from .counter_state import CounterStatePlan
from .life_state import (
    apply_life_changes,
    LifeChange,
    LifeStateError,
    LifeStatePlan,
    plan_life_changes,
    validate_life_changes,
)
from .replacement_effects import (
    AffectedObject,
    ReplaceableEvent,
    ReplacementBatchChoice,
    ReplacementEffect,
    ReplacementEventBatch,
    ReplacementSelection,
    advance_replacement_batch,
)


DamageResultSubjectKind = Literal["player", "permanent"]
DamageResultDirection = Literal["gain", "loss"]


class DamageResultError(ValueError):
    """A dealt-damage batch cannot be converted into exact CR 120.3 results."""


class DamageResultHost(Protocol):
    state: Any
    semantics: Any
    active_seats: list[str]

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def apnap_order(self, *, start: str | None = None) -> list[str]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _queue_siege_defeated_trigger(self, battle: Any) -> None: ...


class DamageMarkerHost(Protocol):
    """Minimal state port for canonical marked-damage ownership."""

    state: Any


@dataclass(frozen=True, slots=True)
class PreparedDamageResults:
    events: tuple[ReplaceableEvent, ...]
    effects: tuple[ReplacementEffect, ...]
    journal: tuple[ReplacementSelection, ...]
    pending: ReplacementBatchChoice | None = None
    consumed_selections: int = 0


@dataclass(frozen=True, slots=True)
class DamageResultRecord:
    event_id: str
    kind: str
    subject_kind: DamageResultSubjectKind
    player: str | None
    object_id: str | None
    amount: int
    requested_amount: int
    direction: DamageResultDirection | None = None
    counter_name: str | None = None
    source: str | None = None
    source_controller: str | None = None
    cause: str = "damage"


@dataclass(frozen=True, slots=True)
class _PermanentStatePlan:
    object_id: str
    marked_damage_after: int
    deathtouch_damage_after: bool
    defeated_battle: bool


@dataclass(frozen=True, slots=True)
class DamageResultCommitPlan:
    life: LifeStatePlan
    permanents: tuple[_PermanentStatePlan, ...]
    counter_placements: CounterPlacementCommitPlan
    counter_removals: CounterRemovalPlan
    records: tuple[DamageResultRecord, ...]
    changed_players: tuple[str, ...]
    changed_objects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DamageResultCommit:
    records: tuple[DamageResultRecord, ...]
    changed_players: tuple[str, ...]
    changed_objects: tuple[str, ...]
    counter_occurrences: tuple[CounterPlacementOccurrence, ...] = ()


@dataclass(slots=True)
class _ResultAccumulator:
    affected_player: str | None
    affected_object: AffectedObject | None
    subject_ref: str
    subject_kind: DamageResultSubjectKind
    target_types: tuple[str, ...]
    children: list[ReplaceableEvent]


@dataclass(frozen=True, slots=True)
class _DamageFacts:
    event_id: str
    amount: int
    target_kind: str
    target: str
    source: str
    source_controller: str
    source_logical: str
    source_types: frozenset[str]
    source_keywords: frozenset[str]
    toxic_value: Any
    combat: bool


@dataclass(slots=True)
class _MaterializationState:
    accumulators: dict[tuple[str, str], _ResultAccumulator]
    life_losses: dict[str, tuple[int, list[str]]]
    life_gains: dict[tuple[str, str, str], tuple[int, list[str]]]
    player_counters: dict[
        tuple[str, str, str, str], tuple[int, list[str]]
    ]
    permanent_counters: dict[
        tuple[str, str, str], tuple[int, list[str], str]
    ]
    permanent_removals: dict[tuple[str, str], tuple[int, list[str]]]
    permanent_marks: dict[str, tuple[int, list[str], bool]]


@dataclass(slots=True)
class _CommitDeltas:
    records: list[DamageResultRecord]
    player_life: dict[str, int]
    player_poison: dict[str, int]
    permanent_counter_add: dict[tuple[str, str], int]
    permanent_counter_remove: dict[tuple[str, str], int]
    permanent_mark: dict[str, int]
    permanent_deathtouch: set[str]
    known_leaf_ids: set[str]


def _integer(
    value: Any,
    *,
    field: str,
    minimum: int = 0,
) -> int:
    if type(value) is not int or value < minimum:
        raise DamageResultError(
            f"Damage result {field} must be an integer of at least {minimum}"
        )
    return value


def _normalized_strings(values: Any) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise DamageResultError("Damage result characteristics must be arrays")
    return tuple(
        sorted(
            {
                " ".join(str(value).casefold().split())
                for value in values
                if str(value).strip()
            }
        )
    )


def _affected_object(host: DamageResultHost, event: ReplaceableEvent) -> tuple[Any, AffectedObject, tuple[str, ...]]:
    object_id = str(event.payload.get("target_object_id") or "")
    logical_object_id = str(
        event.payload.get("target_logical_object_id") or ""
    )
    card = host.state.cards.get(object_id)
    if card is None or card.zone != "battlefield" or card.phased_out:
        raise DamageResultError(
            "Damage result recipient is no longer on the battlefield"
        )
    if card.logical_object_id != logical_object_id:
        raise DamageResultError(
            "Damage result recipient changed object identity"
        )
    data = host._effective_card_data(card)
    card_types, _subtypes, _supertypes = host._type_parts(
        str(data.get("type_line") or "")
    )
    damageable = tuple(
        sorted(card_types.intersection({"battle", "creature", "planeswalker"}))
    )
    if not damageable:
        raise DamageResultError(
            f"Damage result recipient {card.ref} is not damageable"
        )
    return (
        card,
        AffectedObject(
            object_id=card.object_id,
            owner=card.owner,
            controller=card.controller,
        ),
        damageable,
    )


def _subject_key(
    *,
    affected_player: str | None,
    affected_object: AffectedObject | None,
) -> tuple[str, str]:
    if affected_player is not None:
        return ("player", affected_player)
    if affected_object is None:
        raise DamageResultError("A damage result lost its affected subject")
    return ("permanent", affected_object.object_id)


def _ensure_accumulator(
    values: dict[tuple[str, str], _ResultAccumulator],
    *,
    affected_player: str | None,
    affected_object: AffectedObject | None,
    subject_ref: str,
    target_types: Sequence[str] = (),
) -> _ResultAccumulator:
    key = _subject_key(
        affected_player=affected_player,
        affected_object=affected_object,
    )
    current = values.get(key)
    if current is not None:
        return current
    current = _ResultAccumulator(
        affected_player=affected_player,
        affected_object=affected_object,
        subject_ref=subject_ref,
        subject_kind=("player" if affected_player is not None else "permanent"),
        target_types=tuple(sorted(set(target_types))),
        children=[],
    )
    values[key] = current
    return current


def _leaf(
    *,
    event_id: str,
    kind: str,
    affected_player: str | None,
    affected_object: AffectedObject | None,
    payload: Mapping[str, Any],
) -> ReplaceableEvent:
    return ReplaceableEvent(
        event_id=event_id,
        kind=kind,
        affected_player=affected_player,
        affected_object=affected_object,
        payload=dict(payload),
    )


def _append_player_life(
    accumulators: dict[tuple[str, str], _ResultAccumulator],
    *,
    event_id: str,
    player: str,
    amount: int,
    direction: DamageResultDirection,
    source: str | None,
    source_controller: str | None,
    cause: str,
    damage_event_ids: Sequence[str],
) -> None:
    if amount < 1:
        return
    accumulator = _ensure_accumulator(
        accumulators,
        affected_player=player,
        affected_object=None,
        subject_ref=player,
    )
    accumulator.children.append(
        _leaf(
            event_id=event_id,
            kind="life.change",
            affected_player=player,
            affected_object=None,
            payload={
                "target_kind": "player",
                "player": player,
                "direction": direction,
                "amount": amount,
                "requested_amount": amount,
                "source": source,
                "source_controller": source_controller,
                "cause": cause,
                "damage_event_ids": list(damage_event_ids),
            },
        )
    )


def _append_player_counter(
    accumulators: dict[tuple[str, str], _ResultAccumulator],
    *,
    event_id: str,
    player: str,
    amount: int,
    placing_player: str,
    source: str | None,
    cause: str,
    damage_event_ids: Sequence[str],
) -> None:
    if amount < 1:
        return
    accumulator = _ensure_accumulator(
        accumulators,
        affected_player=player,
        affected_object=None,
        subject_ref=player,
    )
    accumulator.children.append(
        _leaf(
            event_id=event_id,
            kind="counter.place",
            affected_player=player,
            affected_object=None,
            payload={
                "target_kind": "player",
                "target_controller": player,
                "target_zone": "player",
                "target_types": [],
                "counter_name": "poison",
                "amount": amount,
                "requested_amount": amount,
                "placing_player": placing_player,
                "source": source,
                "source_controller": placing_player,
                "cause": cause,
                "effect_generated": False,
                "damage_event_ids": list(damage_event_ids),
            },
        )
    )


def _append_permanent_leaf(
    accumulator: _ResultAccumulator,
    *,
    event_id: str,
    kind: str,
    amount: int,
    payload: Mapping[str, Any],
) -> None:
    if amount < 1:
        return
    affected = accumulator.affected_object
    if affected is None:
        raise DamageResultError("Permanent result lost its object identity")
    accumulator.children.append(
        _leaf(
            event_id=event_id,
            kind=kind,
            affected_player=None,
            affected_object=affected,
            payload={
                "target_kind": "permanent",
                "target": accumulator.subject_ref,
                "target_controller": affected.controller,
                "target_zone": "battlefield",
                "target_types": list(accumulator.target_types),
                "amount": amount,
                "requested_amount": amount,
                **dict(payload),
            },
        )
    )


def _clone_nested_result(
    event: ReplaceableEvent,
    *,
    event_id: str,
) -> ReplaceableEvent:
    if event.kind not in {"life.change", "counter.place"}:
        raise DamageResultError(
            f"Damage replacement produced unsupported result {event.kind!r}"
        )
    if event.children:
        raise DamageResultError(
            "Damage-produced result events cannot already contain events"
        )
    return ReplaceableEvent(
        event_id=event_id,
        kind=event.kind,
        affected_player=event.affected_player,
        affected_object=event.affected_object,
        payload=dict(event.payload),
        applied_effects=event.applied_effects,
        entry_scope=event.entry_scope,
    )


def _append_nested_results(
    accumulators: dict[tuple[str, str], _ResultAccumulator],
    damage_event: ReplaceableEvent,
) -> None:
    def visit(current: ReplaceableEvent, path: tuple[int, ...]) -> None:
        if current is not damage_event:
            cloned = _clone_nested_result(
                current,
                event_id=(
                    f"damage.result:nested:{damage_event.event_id}:"
                    + ".".join(str(index) for index in path)
                ),
            )
            if cloned.affected_player is not None:
                accumulator = _ensure_accumulator(
                    accumulators,
                    affected_player=cloned.affected_player,
                    affected_object=None,
                    subject_ref=cloned.affected_player,
                )
            else:
                affected = cloned.affected_object
                if affected is None:
                    raise DamageResultError(
                        "Nested damage result lost its affected subject"
                    )
                accumulator = _ensure_accumulator(
                    accumulators,
                    affected_player=None,
                    affected_object=affected,
                    subject_ref=str(
                        cloned.payload.get("target") or affected.object_id
                    ),
                    target_types=_normalized_strings(
                        cloned.payload.get("target_types", ())
                    ),
                )
            accumulator.children.append(cloned)
            return
        for index, child in enumerate(current.children):
            visit(child, (index,))

    visit(damage_event, ())


def _new_materialization_state() -> _MaterializationState:
    return _MaterializationState(
        accumulators={},
        life_losses={},
        life_gains={},
        player_counters={},
        permanent_counters={},
        permanent_removals={},
        permanent_marks={},
    )


def _damage_facts(
    host: DamageResultHost, damage_event: ReplaceableEvent
) -> _DamageFacts:
    if damage_event.kind != "damage":
        raise DamageResultError("Damage result input must be a damage event")
    payload = damage_event.payload
    facts = _DamageFacts(
        event_id=damage_event.event_id,
        amount=_integer(payload.get("amount"), field="amount"),
        target_kind=str(payload.get("target_kind") or ""),
        target=str(payload.get("target") or ""),
        source=str(payload.get("source") or ""),
        source_controller=str(payload.get("source_controller") or ""),
        source_logical=str(
            payload.get("source_logical_object_id")
            or payload.get("source")
            or ""
        ),
        source_types=frozenset(
            _normalized_strings(payload.get("source_types", ()))
        ),
        source_keywords=frozenset(
            _normalized_strings(payload.get("source_keywords", ()))
        ),
        toxic_value=payload.get("source_toxic_value", 0),
        combat=bool(payload.get("combat")),
    )
    if not facts.source or not facts.source_controller:
        raise DamageResultError(
            "Damage results require stable source and controller facts"
        )
    if facts.amount and facts.source_controller not in host.active_seats:
        raise DamageResultError(
            "A damage-result source controller is no longer in the game"
        )
    return facts


def _accumulate_player_damage(
    host: DamageResultHost,
    state: _MaterializationState,
    facts: _DamageFacts,
) -> None:
    if facts.target not in host.active_seats:
        raise DamageResultError(
            "Damage result recipient is no longer in the game"
        )
    if not facts.amount:
        return
    if "infect" in facts.source_keywords:
        key = (
            facts.target,
            facts.source_controller,
            facts.source_logical,
            "infect",
        )
        prior, event_ids = state.player_counters.get(key, (0, []))
        state.player_counters[key] = (
            prior + facts.amount,
            [*event_ids, facts.event_id],
        )
    else:
        prior, event_ids = state.life_losses.get(facts.target, (0, []))
        state.life_losses[facts.target] = (
            prior + facts.amount,
            [*event_ids, facts.event_id],
        )
    if not (
        facts.combat
        and "creature" in facts.source_types
        and "toxic" in facts.source_keywords
    ):
        return
    if facts.toxic_value is None:
        raise DamageResultError(
            "A toxic source has an unresolved total toxic value"
        )
    toxic_amount = _integer(
        facts.toxic_value,
        field="source_toxic_value",
        minimum=1,
    )
    key = (
        facts.target,
        facts.source_controller,
        facts.source_logical,
        "toxic",
    )
    prior, event_ids = state.player_counters.get(key, (0, []))
    state.player_counters[key] = (
        prior + toxic_amount,
        [*event_ids, facts.event_id],
    )


def _accumulate_permanent_damage(
    host: DamageResultHost,
    state: _MaterializationState,
    facts: _DamageFacts,
    damage_event: ReplaceableEvent,
) -> None:
    _card, affected, damageable = _affected_object(host, damage_event)
    accumulator = _ensure_accumulator(
        state.accumulators,
        affected_player=None,
        affected_object=affected,
        subject_ref=facts.target,
        target_types=damageable,
    )
    if facts.amount and "creature" in damageable:
        has_deathtouch = deathtouch_rules.deathtouch_damage_result_applies(
            amount=facts.amount,
            source_keywords=facts.source_keywords,
            target_types=damageable,
        )
        if facts.source_keywords.intersection({"infect", "wither"}):
            key = (
                affected.object_id,
                facts.source_controller,
                facts.source_logical,
            )
            prior, event_ids, source_ref = state.permanent_counters.get(
                key, (0, [], facts.source)
            )
            state.permanent_counters[key] = (
                prior + facts.amount,
                [*event_ids, facts.event_id],
                source_ref,
            )
        else:
            prior, event_ids, deathtouch = state.permanent_marks.get(
                affected.object_id, (0, [], False)
            )
            state.permanent_marks[affected.object_id] = (
                prior + facts.amount,
                [*event_ids, facts.event_id],
                deathtouch or has_deathtouch,
            )
        if has_deathtouch:
            _append_permanent_leaf(
                accumulator,
                event_id=f"damage.result:deathtouch:{facts.event_id}",
                kind="damage.deathtouch",
                amount=facts.amount,
                payload={
                    "source": facts.source,
                    "source_controller": facts.source_controller,
                    "damage_event_ids": [facts.event_id],
                },
            )
    for card_type, counter_name in (
        ("planeswalker", "loyalty"),
        ("battle", "defense"),
    ):
        if facts.amount and card_type in damageable:
            key = (affected.object_id, counter_name)
            prior, event_ids = state.permanent_removals.get(key, (0, []))
            state.permanent_removals[key] = (
                prior + facts.amount,
                [*event_ids, facts.event_id],
            )


def _accumulate_damage_event(
    host: DamageResultHost,
    state: _MaterializationState,
    damage_event: ReplaceableEvent,
) -> None:
    facts = _damage_facts(host, damage_event)
    if facts.target_kind == "player":
        _accumulate_player_damage(host, state, facts)
    elif facts.target_kind == "permanent":
        _accumulate_permanent_damage(host, state, facts, damage_event)
    else:
        raise DamageResultError("Damage result input lost its recipient")
    if facts.amount and "lifelink" in facts.source_keywords:
        key = (
            facts.source_controller,
            facts.source_logical,
            facts.source,
        )
        prior, event_ids = state.life_gains.get(key, (0, []))
        state.life_gains[key] = (
            prior + facts.amount,
            [*event_ids, facts.event_id],
        )
    _append_nested_results(state.accumulators, damage_event)


def _append_accumulated_player_results(
    state: _MaterializationState,
) -> None:
    for player, (amount, event_ids) in sorted(state.life_losses.items()):
        _append_player_life(
            state.accumulators,
            event_id=f"damage.result:life.loss:{player}",
            player=player,
            amount=amount,
            direction="loss",
            source=None,
            source_controller=None,
            cause="damage",
            damage_event_ids=event_ids,
        )
    for (player, source_logical, source), (amount, event_ids) in sorted(
        state.life_gains.items()
    ):
        _append_player_life(
            state.accumulators,
            event_id=f"damage.result:lifelink:{player}:{source_logical}",
            player=player,
            amount=amount,
            direction="gain",
            source=source,
            source_controller=player,
            cause="lifelink",
            damage_event_ids=event_ids,
        )
    for (player, placing, source_logical, cause), (
        amount,
        event_ids,
    ) in sorted(state.player_counters.items()):
        _append_player_counter(
            state.accumulators,
            event_id=f"damage.result:poison:{cause}:{player}:{source_logical}",
            player=player,
            amount=amount,
            placing_player=placing,
            source=source_logical,
            cause=cause,
            damage_event_ids=event_ids,
        )


def _append_accumulated_permanent_results(
    state: _MaterializationState,
) -> None:
    for (object_id, placing, source_logical), (
        amount,
        event_ids,
        source,
    ) in sorted(state.permanent_counters.items()):
        _append_permanent_leaf(
            state.accumulators[("permanent", object_id)],
            event_id=(
                f"damage.result:counter.minus-one:{object_id}:{source_logical}"
            ),
            kind="counter.place",
            amount=amount,
            payload={
                "counter_name": "-1/-1",
                "placing_player": placing,
                "source": source,
                "source_controller": placing,
                "cause": "infect_or_wither",
                "effect_generated": False,
                "damage_event_ids": event_ids,
            },
        )
    for (object_id, counter_name), (amount, event_ids) in sorted(
        state.permanent_removals.items()
    ):
        _append_permanent_leaf(
            state.accumulators[("permanent", object_id)],
            event_id=f"damage.result:counter.remove:{counter_name}:{object_id}",
            kind="counter.remove",
            amount=amount,
            payload={
                "counter_name": counter_name,
                "cause": "damage",
                "damage_event_ids": event_ids,
            },
        )
    for object_id, (amount, event_ids, deathtouch) in sorted(
        state.permanent_marks.items()
    ):
        _append_permanent_leaf(
            state.accumulators[("permanent", object_id)],
            event_id=f"damage.result:mark:{object_id}",
            kind="damage.mark",
            amount=amount,
            payload={
                "deathtouch": deathtouch,
                "cause": "damage",
                "damage_event_ids": event_ids,
            },
        )


def _controls_creature(host: DamageResultHost, player: str) -> bool:
    return any(
        not host.state.cards[object_id].phased_out
        and "creature"
        in host._type_parts(
            str(
                host._effective_card_data(host.state.cards[object_id]).get(
                    "type_line"
                )
                or ""
            )
        )[0]
        for object_id in host.state.players[player].zones["battlefield"]
    )


def _materialized_root(
    host: DamageResultHost,
    key: tuple[str, str],
    accumulator: _ResultAccumulator,
) -> ReplaceableEvent:
    children = tuple(
        sorted(accumulator.children, key=lambda child: child.event_id)
    )
    life_loss = sum(
        int(child.payload.get("amount", 0))
        for child in children
        if child.kind == "life.change"
        and child.payload.get("direction") == "loss"
    )
    life_gain = sum(
        int(child.payload.get("amount", 0))
        for child in children
        if child.kind == "life.change"
        and child.payload.get("direction") == "gain"
    )
    payload: dict[str, Any] = {
        "subject_kind": accumulator.subject_kind,
        "subject": accumulator.subject_ref,
        "target_types": list(accumulator.target_types),
        "life_loss_amount": life_loss,
        "life_gain_amount": life_gain,
    }
    if accumulator.affected_object is not None:
        payload["subject_logical_object_id"] = host.state.cards[
            accumulator.affected_object.object_id
        ].logical_object_id
    if accumulator.affected_player is not None:
        player = accumulator.affected_player
        life_before = int(host.state.players[player].life)
        payload.update(
            {
                "player": player,
                "life_before": life_before,
                "life_after_without_replacement": (
                    life_before - life_loss + life_gain
                ),
                "controls_creature": _controls_creature(host, player),
            }
        )
    return ReplaceableEvent(
        event_id=(
            f"damage.results:{host.state.revision}:"
            f"{host.state.event_sequence + 1}:{key[0]}:{key[1]}"
        ),
        kind="damage.results",
        affected_player=accumulator.affected_player,
        affected_object=accumulator.affected_object,
        payload=payload,
        children=children,
    )


def materialize_damage_results(
    host: DamageResultHost,
    damage_events: Sequence[ReplaceableEvent],
) -> tuple[ReplaceableEvent, ...]:
    """Convert final dealt components into immutable CR 120.3 result trees."""

    state = _new_materialization_state()
    for damage_event in damage_events:
        _accumulate_damage_event(host, state, damage_event)
    _append_accumulated_player_results(state)
    _append_accumulated_permanent_results(state)
    return tuple(
        _materialized_root(host, key, state.accumulators[key])
        for key in sorted(state.accumulators)
    )
def prepare_damage_results(
    host: DamageResultHost,
    damage_events: Sequence[ReplaceableEvent],
    *,
    effects: Sequence[ReplacementEffect],
    selections: Sequence[str | None] = (),
    require_all_selections: bool = True,
) -> PreparedDamageResults:
    roots = materialize_damage_results(host, damage_events)
    if not roots:
        if selections and require_all_selections:
            raise DamageResultError(
                "Replacement selections were supplied without damage results"
            )
        return PreparedDamageResults(
            events=(),
            effects=tuple(effects),
            journal=(),
            consumed_selections=0,
        )
    progress = advance_replacement_batch(
        ReplacementEventBatch(
            batch_id=(
                f"replacement:damage.results:{host.state.revision}:"
                f"{host.state.event_sequence + 1}"
            ),
            events=roots,
            apnap_order=tuple(host.apnap_order()),
        ),
        effects,
        selections=selections,
        require_all_selections=require_all_selections,
    )
    return PreparedDamageResults(
        events=progress.batch.events,
        effects=tuple(effects),
        journal=progress.batch.journal,
        pending=progress.pending,
        consumed_selections=progress.consumed_selections,
    )


def _result_record(event: ReplaceableEvent) -> DamageResultRecord:
    amount = _integer(event.payload.get("amount"), field="leaf amount")
    requested = _integer(
        event.payload.get("requested_amount", amount),
        field="leaf requested_amount",
    )
    direction: DamageResultDirection | None = None
    if event.kind == "life.change":
        raw_direction = str(event.payload.get("direction") or "")
        if raw_direction not in {"gain", "loss"}:
            raise DamageResultError(
                "Life-change damage results require gain or loss"
            )
        direction = raw_direction  # type: ignore[assignment]
    return DamageResultRecord(
        event_id=event.event_id,
        kind=event.kind,
        subject_kind=(
            "player" if event.affected_player is not None else "permanent"
        ),
        player=event.affected_player,
        object_id=(
            event.affected_object.object_id
            if event.affected_object is not None
            else None
        ),
        amount=amount,
        requested_amount=requested,
        direction=direction,
        counter_name=(
            " ".join(
                str(event.payload.get("counter_name") or "")
                .casefold()
                .split()
            )
            or None
        ),
        source=(
            str(event.payload["source"])
            if event.payload.get("source") is not None
            else None
        ),
        source_controller=(
            str(event.payload["source_controller"])
            if event.payload.get("source_controller") is not None
            else None
        ),
        cause=str(event.payload.get("cause") or "damage"),
    )


def _new_commit_deltas() -> _CommitDeltas:
    return _CommitDeltas(
        records=[],
        player_life={},
        player_poison={},
        permanent_counter_add={},
        permanent_counter_remove={},
        permanent_mark={},
        permanent_deathtouch=set(),
        known_leaf_ids=set(),
    )


def _validate_result_leaf(
    root: ReplaceableEvent,
    child: ReplaceableEvent,
    deltas: _CommitDeltas,
) -> DamageResultRecord:
    if child.children:
        raise DamageResultError(
            "Resolved damage-result leaves cannot contain events"
        )
    if _subject_key(
        affected_player=child.affected_player,
        affected_object=child.affected_object,
    ) != _subject_key(
        affected_player=root.affected_player,
        affected_object=root.affected_object,
    ):
        raise DamageResultError(
            "A damage-result leaf is grouped under the wrong subject"
        )
    if child.event_id in deltas.known_leaf_ids:
        raise DamageResultError(
            "Damage-result leaf IDs must be globally unique"
        )
    deltas.known_leaf_ids.add(child.event_id)
    if child.kind not in {
        "life.change",
        "counter.place",
        "counter.remove",
        "damage.mark",
        "damage.deathtouch",
    }:
        raise DamageResultError(
            f"Unsupported resolved damage result {child.kind!r}"
        )
    return _result_record(child)


def _accumulate_player_leaf(
    host: DamageResultHost,
    child: ReplaceableEvent,
    record: DamageResultRecord,
    deltas: _CommitDeltas,
) -> None:
    player = str(record.player or "")
    if player not in host.active_seats:
        raise DamageResultError(
            "Damage-result player is no longer in the game"
        )
    if child.kind == "life.change":
        sign = 1 if record.direction == "gain" else -1
        deltas.player_life[player] = (
            deltas.player_life.get(player, 0) + sign * record.amount
        )
        return
    if child.kind == "counter.place":
        if record.counter_name != "poison":
            raise DamageResultError(
                "Only poison player counters are represented"
            )
        deltas.player_poison[player] = (
            deltas.player_poison.get(player, 0) + record.amount
        )
        return
    raise DamageResultError(f"{child.kind} cannot affect a player result")


def _validated_permanent_id(
    host: DamageResultHost,
    root: ReplaceableEvent,
    child: ReplaceableEvent,
    record: DamageResultRecord,
) -> str:
    object_id = str(record.object_id or "")
    card = host.state.cards.get(object_id)
    if card is None or card.zone != "battlefield" or card.phased_out:
        raise DamageResultError(
            "Damage-result permanent is no longer on the battlefield"
        )
    if child.affected_object is None:
        raise DamageResultError(
            "Damage-result permanent lost affected-object data"
        )
    if card.logical_object_id != str(
        root.payload.get("subject_logical_object_id") or ""
    ):
        raise DamageResultError(
            "Damage-result permanent changed object identity"
        )
    return object_id


def _accumulate_permanent_leaf(
    host: DamageResultHost,
    root: ReplaceableEvent,
    child: ReplaceableEvent,
    record: DamageResultRecord,
    deltas: _CommitDeltas,
) -> None:
    object_id = _validated_permanent_id(host, root, child, record)
    if child.kind in {"counter.place", "counter.remove"}:
        if not record.counter_name:
            raise DamageResultError(
                f"Counter {child.kind.removeprefix('counter.')} "
                "requires a counter name"
            )
        key = (object_id, record.counter_name)
        destination = (
            deltas.permanent_counter_add
            if child.kind == "counter.place"
            else deltas.permanent_counter_remove
        )
        destination[key] = destination.get(key, 0) + record.amount
        return
    if child.kind == "damage.mark":
        deltas.permanent_mark[object_id] = (
            deltas.permanent_mark.get(object_id, 0) + record.amount
        )
        return
    if child.kind == "damage.deathtouch":
        if record.amount:
            deltas.permanent_deathtouch.add(object_id)
        return
    raise DamageResultError(
        f"{child.kind} cannot affect a permanent result"
    )


def _accumulate_result_root(
    host: DamageResultHost,
    root: ReplaceableEvent,
    deltas: _CommitDeltas,
) -> None:
    if root.kind != "damage.results":
        raise DamageResultError("Damage result roots must be damage.results")
    for child in root.children:
        record = _validate_result_leaf(root, child, deltas)
        deltas.records.append(record)
        if record.subject_kind == "player":
            _accumulate_player_leaf(host, child, record, deltas)
        else:
            _accumulate_permanent_leaf(host, root, child, record, deltas)


def _validate_counter_delta_conflicts(deltas: _CommitDeltas) -> None:
    conflicts = sorted(
        set(deltas.permanent_counter_add).intersection(
            deltas.permanent_counter_remove
        )
    )
    if conflicts:
        raise DamageResultError(
            "Simultaneous placement and removal of the same counter kind "
            "is not yet represented"
        )


def _life_state_plan(
    host: DamageResultHost, deltas: _CommitDeltas
) -> LifeStatePlan:
    try:
        return plan_life_changes(
            host,
            tuple(
                LifeChange(player=player, amount=amount)
                for player, amount in sorted(deltas.player_life.items())
            ),
        )
    except LifeStateError as exc:
        raise DamageResultError(str(exc)) from exc


def _resolved_counter_placement_events(
    host: DamageResultHost,
    prepared: PreparedDamageResults,
) -> tuple[ReplaceableEvent, ...]:
    resolved: list[ReplaceableEvent] = []
    for root in prepared.events:
        logical_object_id = root.payload.get("subject_logical_object_id")
        for child in root.children:
            if child.kind != "counter.place":
                continue
            payload = dict(child.payload)
            if child.affected_object is not None:
                card = host.state.cards.get(child.affected_object.object_id)
                if card is None or card.logical_object_id != logical_object_id:
                    raise DamageResultError(
                        "Damage-result counter subject changed object identity"
                    )
                payload["target_logical_object_id"] = logical_object_id
            resolved.append(
                ReplaceableEvent(
                    event_id=child.event_id,
                    kind=child.kind,
                    affected_player=child.affected_player,
                    affected_object=child.affected_object,
                    payload=payload,
                    applied_effects=child.applied_effects,
                    entry_scope=child.entry_scope,
                )
            )
    return tuple(resolved)


def _counter_placement_plan(
    host: DamageResultHost,
    prepared: PreparedDamageResults,
) -> CounterPlacementCommitPlan:
    try:
        return plan_resolved_counter_placement_commit(
            host,
            _resolved_counter_placement_events(host, prepared),
        )
    except CounterPlacementError as exc:
        raise DamageResultError(str(exc)) from exc


def _counter_removal_plan(
    host: DamageResultHost,
    deltas: _CommitDeltas,
) -> CounterRemovalPlan:
    removals: list[CounterRemoval] = []
    for (object_id, name), requested in sorted(
        deltas.permanent_counter_remove.items()
    ):
        card = host.state.cards[object_id]
        available = card.counters.get(name, 0)
        if type(available) is not int or available < 0:
            raise DamageResultError(
                "Damage-result counter state must be a nonnegative integer"
            )
        # Damage removes counters as its result; it is not a cost that fails
        # when the damage amount exceeds the permanent's counter total.  Pin
        # the exact removable amount before entering the canonical exact-
        # removal transaction so validation and commit still share one owner.
        amount = min(requested, available)
        if amount:
            removals.append(
                CounterRemoval(
                    object_id=object_id,
                    counter_name=name,
                    amount=amount,
                    expected_zone="battlefield",
                    expected_logical_object_id=card.logical_object_id,
                )
            )
    try:
        return plan_counter_removals(
            host,
            tuple(removals),
        )
    except CounterRemovalError as exc:
        raise DamageResultError(str(exc)) from exc


def _counter_after(
    plans: Sequence[CounterStatePlan],
    *,
    subject_kind: str,
    subject_id: str,
    counter_name: str,
    fallback: int,
) -> int:
    result = fallback
    for plan in plans:
        for transition in plan.transitions:
            if (
                transition.subject_kind == subject_kind
                and transition.subject_id == subject_id
                and transition.counter_name == counter_name
            ):
                result = transition.after
    return result


def _permanent_state_plan(
    host: DamageResultHost,
    object_id: str,
    deltas: _CommitDeltas,
    counter_placements: CounterPlacementCommitPlan,
    counter_removals: CounterRemovalPlan,
) -> _PermanentStatePlan:
    card = host.state.cards[object_id]
    before_defense = max(0, int(card.counters.get("defense", 0)))
    after_defense = _counter_after(
        (
            counter_placements.state_plan,
            counter_removals.counter_plan,
        ),
        subject_kind="permanent",
        subject_id=object_id,
        counter_name="defense",
        fallback=before_defense,
    )
    return _PermanentStatePlan(
        object_id=object_id,
        marked_damage_after=(
            int(card.marked_damage) + deltas.permanent_mark.get(object_id, 0)
        ),
        deathtouch_damage_after=(
            bool(card.deathtouch_damage)
            or object_id in deltas.permanent_deathtouch
        ),
        defeated_battle=(
            before_defense > 0 and after_defense == 0
        ),
    )


def _permanent_state_plans(
    host: DamageResultHost,
    deltas: _CommitDeltas,
    counter_placements: CounterPlacementCommitPlan,
    counter_removals: CounterRemovalPlan,
) -> tuple[_PermanentStatePlan, ...]:
    object_ids = set(
        counter_placements.state_plan.changed_objects
    ).union(counter_removals.counter_plan.changed_objects).union(
        deltas.permanent_mark
    ).union(deltas.permanent_deathtouch)
    return tuple(
        _permanent_state_plan(
            host,
            object_id,
            deltas,
            counter_placements,
            counter_removals,
        )
        for object_id in sorted(object_ids)
    )


def plan_damage_result_commit(
    host: DamageResultHost,
    prepared: PreparedDamageResults,
) -> DamageResultCommitPlan:
    """Validate every resolved result and compute one mutation-only commit."""

    if prepared.pending is not None:
        raise DamageResultError(
            "Damage results cannot commit with a pending replacement choice"
        )
    deltas = _new_commit_deltas()
    for root in prepared.events:
        _accumulate_result_root(host, root, deltas)
    _validate_counter_delta_conflicts(deltas)
    counter_placements = _counter_placement_plan(host, prepared)
    counter_removals = _counter_removal_plan(host, deltas)
    life_plan = _life_state_plan(host, deltas)
    permanent_plans = _permanent_state_plans(
        host,
        deltas,
        counter_placements,
        counter_removals,
    )
    return DamageResultCommitPlan(
        life=life_plan,
        permanents=permanent_plans,
        counter_placements=counter_placements,
        counter_removals=counter_removals,
        records=tuple(deltas.records),
        changed_players=tuple(
            sorted(
                set(life_plan.changed_players).union(
                    counter_placements.state_plan.changed_players
                )
            )
        ),
        changed_objects=tuple(
            sorted(
                {plan.object_id for plan in permanent_plans}.union(
                    counter_placements.state_plan.changed_objects
                ).union(
                    counter_removals.counter_plan.changed_objects
                )
            )
        ),
    )


def commit_damage_result_plan(
    host: DamageResultHost,
    plan: DamageResultCommitPlan,
) -> DamageResultCommit:
    """Apply a fully validated result plan without rediscovery or choices."""

    try:
        validate_counter_placement_commit(host, plan.counter_placements)
        validate_counter_removal_plan(host, plan.counter_removals)
        validate_life_changes(host, plan.life)
    except (
        CounterPlacementError,
        CounterRemovalError,
        LifeStateError,
    ) as exc:
        raise DamageResultError(str(exc)) from exc
    commit_counter_placement_plan(
        host,
        plan.counter_placements,
        reason="damage result",
        log=False,
        dispatch_events=False,
    )
    commit_counter_removals(host, plan.counter_removals)
    apply_life_changes(host, plan.life)
    defeated: list[Any] = []
    for permanent_plan in plan.permanents:
        card = host.state.cards[permanent_plan.object_id]
        card.marked_damage = permanent_plan.marked_damage_after
        card.deathtouch_damage = permanent_plan.deathtouch_damage_after
        if permanent_plan.defeated_battle:
            defeated.append(card)
    for battle in defeated:
        host._queue_siege_defeated_trigger(battle)
    from .counter_placement_events import capture_counter_placement_occurrences
    counter_occurrences=capture_counter_placement_occurrences(host, plan.counter_placements.prepared.events, reason="damage result")
    return DamageResultCommit(
        records=plan.records,
        changed_players=plan.changed_players,
        changed_objects=plan.changed_objects,
        counter_occurrences=counter_occurrences,
    )


def consume_deathtouch_damage_checks(
    host: DamageResultHost,
    object_ids: Sequence[str],
) -> tuple[str, ...]:
    """Consume CR 702.2b markers after one state-based-action check."""

    if isinstance(object_ids, (str, bytes)):
        raise DamageResultError(
            "Deathtouch check identities must be a collection"
        )
    values = tuple(object_ids)
    if len(values) != len(set(values)) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise DamageResultError(
            "Deathtouch check identities must be unique nonempty strings"
        )
    cards = []
    for object_id in values:
        card = host.state.cards.get(object_id)
        if card is None or card.zone != "battlefield":
            raise DamageResultError(
                "A deathtouch check recipient changed before consumption"
            )
        cards.append(card)
    for card in cards:
        card.deathtouch_damage = False
    return values


def clear_permanent_damage(
    host: DamageMarkerHost,
    object_id: str,
    *,
    logical_object_id: str,
) -> bool:
    """Clear represented damage from one exact battlefield incarnation."""

    if any(
        type(value) is not str or not value
        for value in (object_id, logical_object_id)
    ):
        raise DamageResultError(
            "Damage clearing requires physical and logical object identity"
        )
    card = host.state.cards.get(object_id)
    if (
        card is None
        or card.zone != "battlefield"
        or bool(card.phased_out)
        or card.logical_object_id != logical_object_id
    ):
        raise DamageResultError(
            "Damage-clearing permanent changed logical identity"
        )
    if type(card.marked_damage) is not int or card.marked_damage < 0:
        raise DamageResultError("Marked-damage state is malformed")
    if type(card.deathtouch_damage) is not bool:
        raise DamageResultError("Deathtouch-damage state is malformed")
    changed = bool(card.marked_damage or card.deathtouch_damage)
    card.marked_damage = 0
    card.deathtouch_damage = False
    return changed
