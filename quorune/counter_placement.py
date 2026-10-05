from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .counter_state import (
    apply_counter_changes,
    CounterChange,
    CounterStatePlan,
    CounterStateError,
    plan_counter_changes,
    validate_counter_changes,
    CounterSubjectKind,
)
from .replacement_effects import (
    ReplaceableEvent,
    ReplacementChoiceRequired,
    ReplacementEffect,
    ReplacementSelection,
)
from .semantic_runtime.counter_replacements import (
    CounterPlacementEventSpec,
    collect_counter_placement_replacement_effects,
    resolve_counter_placement_replacements,
)


class CounterPlacementError(ValueError):
    pass


def validate_counter_event_subjects(
    host: "CounterPlacementHost",
    events: Sequence[ReplaceableEvent],
) -> None:
    """Validate every permanent subject pinned by counter event trees.

    Replacement ordering may suspend a counter event. Resuming must retain the
    original physical and logical subject instead of rebuilding the placement
    around a permanent that left and returned under the same public ref.
    """

    def validate(
        event: ReplaceableEvent,
        parent: ReplaceableEvent | None = None,
    ) -> None:
        if event.kind == "counter.place" and event.affected_object is not None:
            card = host.state.cards.get(event.affected_object.object_id)
            expected_zone = event.payload.get("target_zone")
            expected_logical_id = event.payload.get(
                "target_logical_object_id"
            )
            if event.payload.get("follows_zone_destination") is True:
                if (
                    parent is None
                    or parent.kind != "zone.change"
                    or parent.affected_object is None
                    or parent.affected_object.object_id
                    != event.affected_object.object_id
                    or expected_zone != parent.payload.get("destination")
                ):
                    raise CounterPlacementError(
                        "Prospective zone counter lost its parent event"
                    )
                expected_logical_id = parent.payload.get(
                    "logical_object_id"
                )
                if parent.payload.get("prospective_subject") is not True:
                    expected_zone = parent.payload.get("origin")
                elif (
                    parent.payload.get("origin") != "outside"
                    or expected_zone != "battlefield"
                    or event.payload.get("prospective_subject") is not True
                    or event.payload.get("target_logical_object_id") != expected_logical_id
                ):
                    raise CounterPlacementError("Prospective token counter lost its entry identity")
            prospective_subject = (
                event.payload.get("prospective_subject") is True
            )
            if card is None and prospective_subject:
                if (
                    expected_zone == "battlefield"
                    and type(expected_logical_id) is str
                    and bool(expected_logical_id)
                ):
                    return
            if (
                card is None
                or type(expected_zone) is not str
                or not expected_zone
                or type(expected_logical_id) is not str
                or not expected_logical_id
                or card.zone != expected_zone
                or card.logical_object_id != expected_logical_id
            ):
                raise CounterPlacementError(
                    "Counter replacement continuation subject changed"
                )
        for child in event.children:
            validate(child, event)

    for root in events:
        if not isinstance(root, ReplaceableEvent):
            raise CounterPlacementError(
                "Counter subject validation requires typed events"
            )
        validate(root)


class CounterPlacementHost(Protocol):
    state: Any
    semantics: Any
    active_seats: list[str]

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def apnap_order(self, *, start: str | None = None) -> list[str]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str] | None = None,
    ) -> Any: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> None: ...


class CounterEventTreeResolution(Protocol):
    event: ReplaceableEvent | None
    effects: Sequence[ReplacementEffect]
    journal: Sequence[ReplacementSelection]


@dataclass(frozen=True, slots=True)
class CounterPlacementRequest:
    subject_kind: CounterSubjectKind
    subject_id: str
    counter_name: str
    amount: int
    placing_player: str
    source_ref: str | None = None
    effect_generated: bool = True

    def __post_init__(self) -> None:
        normalized = " ".join(self.counter_name.casefold().split())
        if self.subject_kind not in {"player", "permanent"}:
            raise CounterPlacementError(
                "Counter placement subjects must be players or permanents"
            )
        if not self.subject_id or not normalized:
            raise CounterPlacementError(
                "Counter placements require a subject and counter name"
            )
        if type(self.amount) is not int or self.amount < 0:
            raise CounterPlacementError(
                "Counter placement amounts cannot be negative"
            )
        if not self.placing_player:
            raise CounterPlacementError(
                "Counter placements require the placing player"
            )

    @property
    def normalized_name(self) -> str:
        return " ".join(self.counter_name.casefold().split())


@dataclass(frozen=True, slots=True)
class PreparedCounterPlacements:
    events: tuple[ReplaceableEvent, ...]
    effects: tuple[ReplacementEffect, ...]
    journal: tuple[ReplacementSelection, ...]


@dataclass(frozen=True, slots=True)
class CounterPlacementResult:
    subject_kind: CounterSubjectKind
    subject_id: str
    counter_name: str
    requested: int
    placed: int
    before: int
    after: int

    @property
    def object_id(self) -> str:
        """Compatibility accessor for permanent-only placement consumers."""

        if self.subject_kind != "permanent":
            raise CounterPlacementError(
                "Player counter placements do not have an object ID"
            )
        return self.subject_id


@dataclass(frozen=True, slots=True)
class CounterPlacementCommitRow:
    event: ReplaceableEvent
    subject_kind: CounterSubjectKind
    subject_id: str
    display_ref: str
    counter_name: str
    requested: int
    amount: int


@dataclass(frozen=True, slots=True)
class CounterPlacementCommitPlan:
    prepared: PreparedCounterPlacements
    rows: tuple[CounterPlacementCommitRow, ...]
    state_plan: CounterStatePlan


def _event_spec(
    host: CounterPlacementHost,
    request: CounterPlacementRequest,
    *,
    event_id: str,
) -> CounterPlacementEventSpec:
    if request.subject_kind == "player":
        if request.subject_id not in host.state.players:
            raise CounterPlacementError(
                "Counter placement player no longer exists"
            )
        return CounterPlacementEventSpec(
            event_id=event_id,
            subject_kind="player",
            subject_id=request.subject_id,
            placing_player=request.placing_player,
            counter_name=request.normalized_name,
            amount=request.amount,
            source_ref=request.source_ref,
            effect_generated=request.effect_generated,
        )
    card = host.state.cards.get(request.subject_id)
    if card is None:
        raise CounterPlacementError(
            "Counter placement target no longer exists"
        )
    data = host._effective_card_data(card)
    card_types, subtypes, supertypes = host._type_parts(
        str(data.get("type_line") or "")
    )
    controller = card.controller if card.zone == "battlefield" else None
    return CounterPlacementEventSpec(
        event_id=event_id,
        subject_kind="permanent",
        subject_id=card.object_id,
        placing_player=request.placing_player,
        counter_name=request.normalized_name,
        amount=request.amount,
        source_ref=request.source_ref,
        effect_generated=request.effect_generated,
        owner=card.owner,
        controller=controller,
        target_zone=card.zone,
        target_types=tuple(
            sorted({*card_types, *subtypes, *supertypes})
        ),
        logical_object_id=card.logical_object_id,
    )


def _event_subject_label(
    host: CounterPlacementHost,
    request: CounterPlacementRequest,
) -> str:
    if request.subject_kind == "player":
        return request.subject_id
    card = host.state.cards.get(request.subject_id)
    if card is None:
        raise CounterPlacementError(
            "Counter placement target no longer exists"
        )
    return card.ref


def prepare_counter_placements(
    host: CounterPlacementHost,
    requests: Sequence[CounterPlacementRequest],
    *,
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
    event_ids: Sequence[str] | None = None,
) -> PreparedCounterPlacements:
    """Resolve one simultaneous counter-placement batch before mutation."""

    nonzero = tuple(request for request in requests if request.amount > 0)
    if event_ids is not None and (
        isinstance(event_ids, (str, bytes, bytearray))
        or not isinstance(event_ids, Sequence)
    ):
        raise CounterPlacementError(
            "Pinned counter-placement event IDs must be a sequence"
        )
    pinned_event_ids = tuple(event_ids or ())
    if event_ids is not None and (
        len(pinned_event_ids) != len(nonzero)
        or any(
            type(value) is not str or not value or value != value.strip()
            for value in pinned_event_ids
        )
        or len(pinned_event_ids) != len(set(pinned_event_ids))
    ):
        raise CounterPlacementError(
            "Pinned counter-placement event IDs must be nonempty and unique"
        )
    if not nonzero:
        if selections:
            raise CounterPlacementError(
                "Replacement selections were supplied without counters"
            )
        return PreparedCounterPlacements(events=(), effects=(), journal=())
    specs = tuple(
        _event_spec(
            host,
            request,
            event_id=(
                pinned_event_ids[index]
                if event_ids is not None
                else (
                    f"counter.place:{host.state.revision}:"
                    f"{host.state.event_sequence + 1}:{index}:"
                    f"{_event_subject_label(host, request)}"
                )
            ),
        )
        for index, request in enumerate(nonzero)
    )
    return prepare_counter_placement_specs(
        host,
        specs,
        selections=selections,
        sources=sources,
        source_zones=source_zones,
        batch_id=(
            f"replacement:counter.place:{host.state.revision}:"
            f"{host.state.event_sequence + 1}"
        ),
    )


def prepare_counter_placement_specs(
    host: CounterPlacementHost,
    specs: Sequence[CounterPlacementEventSpec],
    *,
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
    batch_id: str,
) -> PreparedCounterPlacements:
    """Resolve typed placement specs whose subjects may be prospective.

    Replacement discovery and ordering remain pure. Commit later requires the
    exact subject and logical identity encoded by every event to exist in its
    declared zone.
    """

    if type(batch_id) is not str or not batch_id or batch_id != batch_id.strip():
        raise CounterPlacementError(
            "Counter-placement batches require a stable nonempty ID"
        )
    if isinstance(specs, (str, bytes, bytearray)) or not isinstance(
        specs, Sequence
    ):
        raise CounterPlacementError(
            "Counter-placement specs must be a sequence"
        )
    if any(not isinstance(spec, CounterPlacementEventSpec) for spec in specs):
        raise CounterPlacementError(
            "Counter-placement specs must be typed"
        )
    events = tuple(spec.event() for spec in specs)
    event_ids = tuple(event.event_id for event in events)
    if len(event_ids) != len(set(event_ids)):
        raise CounterPlacementError(
            "Counter-placement event IDs must be unique"
        )
    if not events:
        if selections:
            raise CounterPlacementError(
                "Replacement selections were supplied without counters"
            )
        return PreparedCounterPlacements(events=(), effects=(), journal=())
    effects = collect_counter_placement_replacement_effects(
        host,
        sources=sources,
        source_zones=source_zones,
    )
    if not effects:
        if selections:
            raise CounterPlacementError(
                "Replacement selections were supplied without an applicable "
                "counter replacement"
            )
        return PreparedCounterPlacements(
            events=events,
            effects=(),
            journal=(),
        )
    resolution = resolve_counter_placement_replacements(
        batch_id=batch_id,
        events=events,
        effects=effects,
        apnap_order=host.apnap_order(),
        selections=selections,
    )
    if resolution.pending is not None:
        raise ReplacementChoiceRequired(
            batch=resolution.batch,
            effects=effects,
            pending=resolution.pending,
        )
    return PreparedCounterPlacements(
        events=resolution.batch.events,
        effects=effects,
        journal=resolution.journal,
    )


def _resolved_amount(event: ReplaceableEvent) -> tuple[str, int, int]:
    name = " ".join(
        str(event.payload.get("counter_name") or "").casefold().split()
    )
    requested = int(event.payload.get("requested_amount", 0))
    amount = int(event.payload.get("amount", -1))
    if not name or requested < 1 or amount < 0:
        raise CounterPlacementError(
            "Resolved counter placement produced invalid data"
        )
    return name, requested, amount


def log_counter_placement_replacements(
    host: CounterPlacementHost,
    prepared: PreparedCounterPlacements,
) -> None:
    """Journal applied counter replacements without duplicating mutation."""

    effects = {effect.effect_id: effect for effect in prepared.effects}
    events = {event.event_id: event for event in prepared.events}
    for selection in prepared.journal:
        selected_id = str(selection.effect_id or "")
        if selected_id.startswith("decline:"):
            continue
        effect = effects.get(selected_id)
        event = events.get(selection.event_id)
        if effect is None or event is None:
            raise CounterPlacementError(
                "Counter replacement journal does not match its snapshot"
            )
        name, requested, amount = _resolved_amount(event)
        object_id = (
            event.affected_object.object_id
            if event.affected_object is not None
            else None
        )
        player = event.affected_player
        host._log(
            None,
            "replacement.apply",
            f"{effect.source_id} changed a counter placement.",
            {
                "source": effect.source_id,
                "effect_id": effect.effect_id,
                "object_id": object_id,
                "player": player,
                "counter": name,
                "requested": requested,
                "resolved": amount,
            },
            importance=2,
            changed_objects=([object_id] if object_id is not None else []),
            changed_players=([player] if player is not None else []),
        )


def plan_prepared_counter_placement_commit(
    host: CounterPlacementHost,
    prepared: PreparedCounterPlacements,
) -> CounterPlacementCommitPlan:
    """Validate a resolved placement batch and pin its mutation plan."""

    if not isinstance(prepared, PreparedCounterPlacements):
        raise CounterPlacementError(
            "Counter placement commits require a typed prepared batch"
        )
    validated: list[CounterPlacementCommitRow] = []
    for event in prepared.events:
        if event.kind != "counter.place" or event.children:
            raise CounterPlacementError(
                "Counter placement commits require resolved placement leaves"
            )
        name, requested, amount = _resolved_amount(event)
        target_kind = str(event.payload.get("target_kind") or "")
        if target_kind == "player":
            player = event.affected_player
            if player is None or event.affected_object is not None:
                raise CounterPlacementError(
                    "Player counter placement lost its affected player"
                )
            if player not in host.state.players:
                raise CounterPlacementError(
                    "Counter placement player no longer exists"
                )
            validated.append(
                CounterPlacementCommitRow(
                    event=event,
                    subject_kind="player",
                    subject_id=player,
                    display_ref=player,
                    counter_name=name,
                    requested=requested,
                    amount=amount,
                )
            )
            continue
        affected = event.affected_object
        if affected is None or event.affected_player is not None:
            raise CounterPlacementError(
                "Counter placement event lost its affected object"
            )
        card = host.state.cards.get(affected.object_id)
        if card is None:
            raise CounterPlacementError(
                "Counter placement target no longer exists"
            )
        target_zone = str(event.payload.get("target_zone") or "")
        if card.zone != target_zone:
            raise CounterPlacementError(
                "Counter placement target changed zones before commit"
            )
        validated.append(
            CounterPlacementCommitRow(
                event=event,
                subject_kind="permanent",
                subject_id=card.object_id,
                display_ref=card.ref,
                counter_name=name,
                requested=requested,
                amount=amount,
            )
        )

    try:
        state_plan = plan_counter_changes(
            host,
            tuple(
                CounterChange(
                    subject_kind=row.subject_kind,
                    subject_id=row.subject_id,
                    counter_name=row.counter_name,
                    amount=row.amount,
                    expected_zone=(
                        str(row.event.payload.get("target_zone") or "")
                        if row.subject_kind == "permanent"
                        else None
                    ),
                    expected_logical_object_id=(
                        str(row.event.payload["target_logical_object_id"])
                        if row.subject_kind == "permanent"
                        and row.event.payload.get("target_logical_object_id")
                        is not None
                        else None
                    ),
                )
                for row in validated
            ),
        )
    except CounterStateError as exc:
        raise CounterPlacementError(str(exc)) from exc
    return CounterPlacementCommitPlan(
        prepared=prepared,
        rows=tuple(validated),
        state_plan=state_plan,
    )


def plan_resolved_counter_placement_commit(
    host: CounterPlacementHost,
    events: Sequence[ReplaceableEvent],
) -> CounterPlacementCommitPlan:
    """Plan leaves already resolved by a containing replacement tree.

    This boundary is for typed event coordinators such as damage results. It
    deliberately performs no replacement discovery: the containing tree owns
    that ordering and supplies its final immutable ``counter.place`` leaves.
    """

    stable = tuple(events)
    if any(not isinstance(event, ReplaceableEvent) for event in stable):
        raise CounterPlacementError(
            "Resolved counter placements must be typed events"
        )
    event_ids = tuple(event.event_id for event in stable)
    if (
        any(not event_id for event_id in event_ids)
        or len(event_ids) != len(set(event_ids))
    ):
        raise CounterPlacementError(
            "Resolved counter-placement event IDs must be nonempty and unique"
        )
    return plan_prepared_counter_placement_commit(
        host,
        PreparedCounterPlacements(
            events=stable,
            effects=(),
            journal=(),
        ),
    )


def validate_counter_placement_commit(
    host: CounterPlacementHost,
    plan: CounterPlacementCommitPlan,
) -> None:
    if not isinstance(plan, CounterPlacementCommitPlan):
        raise CounterPlacementError(
            "Counter placement validation requires a typed commit plan"
        )
    try:
        validate_counter_changes(host, plan.state_plan)
    except CounterStateError as exc:
        raise CounterPlacementError(str(exc)) from exc


def commit_counter_placement_plan(
    host: CounterPlacementHost,
    plan: CounterPlacementCommitPlan,
    *,
    reason: str,
    log: bool = True,
) -> tuple[CounterPlacementResult, ...]:
    """Apply a validated placement plan without rediscovering replacements."""

    validate_counter_placement_commit(host, plan)
    try:
        transitions = apply_counter_changes(host, plan.state_plan)
    except CounterStateError as exc:
        raise CounterPlacementError(str(exc)) from exc

    results: list[CounterPlacementResult] = []
    for row, transition in zip(
        plan.rows, transitions, strict=True
    ):
        results.append(
            CounterPlacementResult(
                subject_kind=row.subject_kind,
                subject_id=row.subject_id,
                counter_name=row.counter_name,
                requested=row.requested,
                placed=row.amount,
                before=transition.before,
                after=transition.after,
            )
        )
        if log:
            changed_objects = (
                [row.subject_id]
                if row.subject_kind == "permanent"
                else []
            )
            changed_players = (
                [row.subject_id]
                if row.subject_kind == "player"
                else []
            )
            host._log(
                str(row.event.payload.get("placing_player") or "") or None,
                "counter.add",
                f"Put {row.amount} {row.counter_name} counter(s) on "
                f"{row.display_ref}.",
                {
                    (
                        "object"
                        if row.subject_kind == "permanent"
                        else "player"
                    ): row.display_ref,
                    "counter": row.counter_name,
                    "requested": row.requested,
                    "placed": row.amount,
                    "before": transition.before,
                    "after": transition.after,
                    "source": row.event.payload.get("source"),
                    "placement_reason": reason,
                },
                importance=2,
                changed_objects=changed_objects,
                changed_players=changed_players,
            )
    if log:
        log_counter_placement_replacements(host, plan.prepared)
    return tuple(results)


def commit_prepared_counter_placements(
    host: CounterPlacementHost,
    prepared: PreparedCounterPlacements,
    *,
    reason: str,
    log: bool = True,
) -> tuple[CounterPlacementResult, ...]:
    """Commit a choice-complete batch without rediscovering effects."""

    return commit_counter_placement_plan(
        host,
        plan_prepared_counter_placement_commit(host, prepared),
        reason=reason,
        log=log,
    )


def place_counters(
    host: CounterPlacementHost,
    requests: Sequence[CounterPlacementRequest],
    *,
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    reason: str,
    log: bool = True,
) -> tuple[CounterPlacementResult, ...]:
    prepared = prepare_counter_placements(
        host,
        requests,
        selections=selections,
    )
    return commit_prepared_counter_placements(
        host,
        prepared,
        reason=reason,
        log=log,
    )


def place_counters_on_refs(
    host: CounterPlacementHost,
    *,
    actor: str,
    object_refs: Sequence[str],
    counter_name: str,
    amount: int,
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    reason: str,
    source_ref: str | None = None,
) -> tuple[CounterPlacementResult, ...]:
    """Resolve battlefield refs and route one effect placement batch."""

    cards = tuple(
        host._resolve_object(actor, ref, zones={"battlefield"})
        for ref in object_refs
    )
    return place_counters(
        host,
        tuple(
            CounterPlacementRequest(
                subject_kind="permanent",
                subject_id=card.object_id,
                counter_name=counter_name,
                amount=amount,
                placing_player=actor,
                source_ref=source_ref,
            )
            for card in cards
        ),
        selections=selections,
        reason=reason,
    )


def place_counters_on_controlled_subtype(
    host: CounterPlacementHost,
    *,
    actor: str,
    controller: str,
    subtype: str,
    counter_name: str,
    amount: int,
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    reason: str,
    source_ref: str | None = None,
) -> tuple[CounterPlacementResult, ...]:
    """Build one simultaneous batch from a controller's effective subtype."""

    normalized_subtype = " ".join(subtype.casefold().split())
    if not normalized_subtype:
        raise CounterPlacementError(
            "Subtype counter placement requires a subtype"
        )
    refs = tuple(
        card.ref
        for object_id in host.state.players[controller].zones["battlefield"]
        for card in (host.state.cards[object_id],)
        if card.controller == controller
        and not card.phased_out
        and normalized_subtype
        in host._type_parts(
            str(host._effective_card_data(card).get("type_line") or "")
        )[1]
    )
    return place_counters_on_refs(
        host,
        actor=actor,
        object_refs=refs,
        counter_name=counter_name,
        amount=amount,
        selections=selections,
        reason=reason,
        source_ref=source_ref,
    )


def prepared_counter_events_from_tree(
    event: ReplaceableEvent,
    *,
    effects: Sequence[ReplacementEffect],
    journal: Sequence[ReplacementSelection],
) -> PreparedCounterPlacements:
    """Extract resolved nested counter events from a containing event."""

    counter_events: list[ReplaceableEvent] = []

    def visit(current: ReplaceableEvent) -> None:
        if current.kind == "counter.place":
            counter_events.append(current)
        for child in current.children:
            visit(child)

    visit(event)
    counter_ids = {current.event_id for current in counter_events}
    return PreparedCounterPlacements(
        events=tuple(counter_events),
        effects=tuple(
            effect for effect in effects if effect.event_kind == "counter.place"
        ),
        journal=tuple(
            selection
            for selection in journal
            if selection.event_id in counter_ids
        ),
    )


def commit_counter_events_from_resolution(
    host: CounterPlacementHost,
    resolution: CounterEventTreeResolution,
    *,
    reason: str,
    log: bool,
    error_type: type[Exception] = CounterPlacementError,
) -> tuple[CounterPlacementResult, ...]:
    """Commit resolved nested counters without growing the zone-move owner."""

    if resolution.event is None:
        return ()
    try:
        return commit_prepared_counter_placements(
            host,
            prepared_counter_events_from_tree(
                resolution.event,
                effects=resolution.effects,
                journal=resolution.journal,
            ),
            reason=reason,
            log=log,
        )
    except CounterPlacementError as exc:
        raise error_type(str(exc)) from exc
