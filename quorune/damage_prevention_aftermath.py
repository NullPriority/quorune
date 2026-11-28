from __future__ import annotations
from .counter_placement_event_model import CounterPlacementOccurrence

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from .counter_placement import (
    commit_counter_placement_plan,
    counter_placement_event_spec,
    CounterPlacementCommitPlan,
    CounterPlacementError,
    CounterPlacementRequest,
    plan_prepared_counter_placement_commit,
    prepare_counter_placements,
    validate_counter_placement_commit,
)
from .damage_modifier_state import (
    CreateTokensPreventionAftermath,
    DealDamagePreventionAftermath,
    GainLifePreventionAftermath,
    PlaceCountersPreventionAftermath,
)
from .damage_prevention import (
    DamageModifierSnapshot,
    project_damage_modifier_snapshot,
)
from .damage_transaction import (
    DamageTransactionPort,
    DamageTransactionResult,
    PreparedDamageTransaction,
)
from .counter_prevention import (
    PreparedCounterPrevention, validate_nested_counter_prevention, commit_counter_prevention_results,
    projected_counter_prevention_host,
)
from .damage_results import DamageResultRecord
from .damage_values import DamageProposal
from .life_gain_permission import LifeGainPermissionSnapshot
from .life_change import (
    commit_life_change_batch,
    LifeChangeError,
    LifeChangeRequest,
    life_change_request_event,
    PreparedLifeChangeBatch,
    prepare_life_change_batch,
    replan_life_change_batch,
    validate_life_change_batch,
)
from .replacement import (
    PreventAmount,
    ReplaceableEvent,
    ReplacementChoiceRequired,
    ReplacementEffect,
    ReplacementEventBatch,
)
from .replacement.operations import UNPREVENTABLE_DAMAGE_ATTEMPTS
from .semantic_runtime.life_replacements import (
    collect_life_change_replacement_effects,
)
from .semantic_runtime.life_prohibitions import collect_life_gain_permissions


class PreventionAftermathError(ValueError):
    """A CR 615.5 immediately-after result is malformed or stale."""


class PreventionAftermathHost(Protocol):
    state: Any

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def apnap_order(self, *, start: str | None = None) -> list[str]: ...

    def _log(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class PreventionApplication:
    effect_id: str
    source_id: str
    prevented_amount: int
    damage_event_ids: tuple[str, ...]
    damage_source_controllers: tuple[str, ...]
    unpreventable_amount: int = 0


@dataclass(frozen=True, slots=True)
class PreparedAftermathInstruction:
    effect_id: str
    source_id: str
    kind: str
    subject: str
    prevented_amount: int
    requested_amount: int
    damage_event_ids: tuple[str, ...]
    result_event_id: str | None = None
    nested_damage: PreparedDamageTransaction | None = None


@dataclass(frozen=True, slots=True)
class PreparedPreventionAftermath:
    applications: tuple[PreventionApplication, ...] = ()
    instructions: tuple[PreparedAftermathInstruction, ...] = ()
    life_batch: PreparedLifeChangeBatch | None = None
    counter_plan: CounterPlacementCommitPlan | None = None
    consumed_selections: int = 0
    counter_removals: tuple[PreparedCounterPrevention, ...] = ()
    in_damage_results: bool = False


@dataclass(frozen=True, slots=True)
class PreventionAftermathEvent:
    effect_id: str
    source_id: str
    kind: str
    subject: str
    prevented_amount: int
    applied_amount: int
    damage_event_ids: tuple[str, ...]

    def semantic_context(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "source": self.source_id,
            "kind": self.kind,
            "subject": self.subject,
            "prevented_amount": self.prevented_amount,
            "applied_amount": self.applied_amount,
            "damage_event_ids": list(self.damage_event_ids),
        }


@dataclass(frozen=True, slots=True)
class PreventionAftermathResult:
    events: tuple[PreventionAftermathEvent, ...] = ()
    changed_players: tuple[str, ...] = ()
    changed_objects: tuple[str, ...] = ()
    nested_damage_results: tuple[DamageTransactionResult, ...] = ()
    counter_occurrences: tuple[CounterPlacementOccurrence, ...] = ()


@dataclass(frozen=True, slots=True)
class PreventionAftermathRequests:
    applications: tuple[PreventionApplication, ...]
    instructions: tuple[PreparedAftermathInstruction, ...]
    life: tuple[LifeChangeRequest, ...]
    counters: tuple[CounterPlacementRequest, ...]
    damage: tuple[
        tuple[PreventionApplication, int, DealDamagePreventionAftermath, int],
        ...,
    ]


def _prevention_aftermath_sources(host, effects):
    sources = {
        shield.effect_id: (shield.source_id, shield.controller, shield.aftermath)
        for shield in host.state.damage_prevention_shields
        if shield.aftermath
    }
    for effect in effects:
        for operation in effect.operations:
            if not isinstance(operation, PreventAmount) or operation.life_gain_player is None:
                continue
            if effect.effect_id in sources:
                raise PreventionAftermathError("Prevention aftermath requires one source per effect")
            player = operation.life_gain_player
            sources[effect.effect_id] = (
                effect.source_id, player, (GainLifePreventionAftermath(player=player, per_prevented=1),),
            )
    return sources


def prevention_applications(
    host: PreventionAftermathHost,
    events: Sequence[ReplaceableEvent],
) -> tuple[PreventionApplication, ...]:
    """Aggregate one stored shield application across a simultaneous damage batch."""
    return _applied_prevention_aftermath(events, _prevention_aftermath_sources(host, ()))


def _applied_prevention_aftermath(events, sources) -> tuple[PreventionApplication, ...]:
    amounts: dict[str, int] = {}
    attempts: dict[str, int] = {}
    event_ids: dict[str, list[str]] = {}
    source_controllers: dict[str, set[str]] = {}
    applied: set[str] = set()
    for event in events:
        by_effect = event.payload.get("prevention_applied") or {}
        attempted = event.payload.get(UNPREVENTABLE_DAMAGE_ATTEMPTS, {})
        if not isinstance(attempted, Mapping):
            raise PreventionAftermathError("Unpreventable damage amount data is malformed")
        for effect_id, amount in attempted.items():
            if effect_id in sources:
                if type(amount) is not int or amount < 1:
                    raise PreventionAftermathError("Unpreventable damage amount is malformed")
                attempts[effect_id] = attempts.get(effect_id, 0) + amount
        if not isinstance(by_effect, Mapping):
            raise PreventionAftermathError(
                "Resolved prevention application data is malformed"
            )
        for effect_id in event.applied_effects:
            if effect_id in sources:
                applied.add(effect_id)
                event_ids.setdefault(effect_id, []).append(event.event_id)
                controller = str(
                    event.payload.get("source_controller") or ""
                )
                if controller:
                    source_controllers.setdefault(effect_id, set()).add(
                        controller
                    )
        for raw_id, raw_amount in by_effect.items():
            effect_id = str(raw_id)
            if effect_id not in sources:
                continue
            if type(raw_amount) is not int or raw_amount < 0:
                raise PreventionAftermathError(
                    "Resolved prevention amount is malformed"
                )
            applied.add(effect_id)
            amounts[effect_id] = amounts.get(effect_id, 0) + raw_amount
            if event.event_id not in event_ids.setdefault(effect_id, []):
                event_ids[effect_id].append(event.event_id)
    return tuple(
        PreventionApplication(
            effect_id=effect_id,
            source_id=sources[effect_id][0],
            prevented_amount=amounts.get(effect_id, 0),
            unpreventable_amount=attempts.get(effect_id, 0),
            damage_event_ids=tuple(sorted(set(event_ids.get(effect_id, ())))),
            damage_source_controllers=tuple(
                sorted(source_controllers.get(effect_id, ()))
            ),
        )
        for effect_id in sorted(applied)
    )


def prevention_aftermath_requests(
    host: PreventionAftermathHost,
    events: Sequence[ReplaceableEvent],
    *,
    effects: Sequence[ReplacementEffect] = (),
    in_damage_results: bool = False,
) -> PreventionAftermathRequests:
    sources = _prevention_aftermath_sources(host, effects)
    applications = _applied_prevention_aftermath(events, sources)
    instructions: list[PreparedAftermathInstruction] = []
    life: list[LifeChangeRequest] = []
    counters: list[CounterPlacementRequest] = []
    damage: list[
        tuple[PreventionApplication, int, DealDamagePreventionAftermath, int]
    ] = []
    for application in applications:
        _, controller, aftermath_effects = sources[application.effect_id]
        for index, aftermath in enumerate(aftermath_effects):
            if isinstance(aftermath, CreateTokensPreventionAftermath):
                continue
            damage_amount = application.prevented_amount
            if isinstance(aftermath, PlaceCountersPreventionAftermath) and aftermath.counts_unpreventable:
                damage_amount += application.unpreventable_amount
            amount = aftermath.amount(damage_amount)
            if amount == 0:
                continue
            result_event_id: str | None = None
            if isinstance(aftermath, GainLifePreventionAftermath):
                result_event_id = (
                    "damage.prevention.aftermath:life:"
                    f"{application.effect_id}:{index}"
                )
                life.append(
                    LifeChangeRequest(
                        event_id=result_event_id,
                        player=aftermath.player,
                        amount=amount,
                        source=application.source_id,
                        source_controller=controller,
                        cause="damage_prevention_aftermath",
                    )
                )
                kind, subject = "gain_life", aftermath.player
            elif isinstance(aftermath, PlaceCountersPreventionAftermath):
                assert aftermath.subject.object_id is not None
                if in_damage_results:
                    subject = host.state.cards.get(aftermath.subject.object_id)
                    if (
                        subject is None or subject.zone != "battlefield" or subject.phased_out
                        or subject.logical_object_id != aftermath.subject.logical_object_id
                    ):
                        continue
                result_event_id = f"damage.prevention.aftermath:counter:{application.effect_id}:{index}"
                counters.append(
                    CounterPlacementRequest(
                        subject_kind="permanent",
                        subject_id=aftermath.subject.object_id,
                        counter_name=aftermath.counter_name,
                        amount=amount,
                        placing_player=aftermath.placing_player,
                        source_ref=application.source_id,
                    )
                )
                kind, subject = "place_counters", aftermath.subject.ref
            elif isinstance(aftermath, DealDamagePreventionAftermath):
                damage.append((application, index, aftermath, amount))
                continue
            else:  # pragma: no cover - shield validation closes this union.
                raise PreventionAftermathError(
                    "Unsupported prevention aftermath operation"
                )
            instructions.append(
                PreparedAftermathInstruction(
                    effect_id=application.effect_id,
                    source_id=application.source_id,
                    kind=kind,
                    subject=subject,
                    prevented_amount=application.prevented_amount,
                    requested_amount=amount,
                    damage_event_ids=application.damage_event_ids,
                    result_event_id=result_event_id,
                )
            )
    return PreventionAftermathRequests(
        applications=applications,
        instructions=tuple(instructions),
        life=tuple(life),
        counters=tuple(counters),
        damage=tuple(damage),
    )


def prevention_result_events(
    host: PreventionAftermathHost,
    requests: PreventionAftermathRequests,
    gain_permissions: LifeGainPermissionSnapshot,
) -> tuple[ReplaceableEvent, ...]:
    """Bind immediate life and counter instructions to the containing damage result."""
    instructions = {row.result_event_id: row for row in requests.instructions}
    events: list[ReplaceableEvent] = []
    for request in requests.life:
        event = life_change_request_event(request)
        events.append(replace(event, payload={
            **event.payload,
            "target_kind": "player",
            "amount": request.amount if gain_permissions.allows_gain(request.player) else 0,
            "damage_event_ids": list(instructions[event.event_id].damage_event_ids),
        }))
    counter_rows = (row for row in requests.instructions if row.kind == "place_counters")
    for row, request in zip(counter_rows, requests.counters, strict=True):
        assert row.result_event_id is not None
        spec = counter_placement_event_spec(host, request, event_id=row.result_event_id)
        event = spec.event()
        events.append(replace(event, payload={
            **event.payload,
            "target": row.subject,
            "source_controller": request.placing_player,
            "cause": "damage_prevention_aftermath",
            "damage_event_ids": list(row.damage_event_ids),
        }))
    return tuple(events)


def _prepare_damage_aftermath(
    damage_port: DamageTransactionPort,
    values: Sequence[
        tuple[PreventionApplication, int, DealDamagePreventionAftermath, int]
    ],
    *,
    selections: Sequence[str | None],
    sources: Sequence[object] | None,
    source_zones: Mapping[str, str] | None,
    modifier_snapshot: DamageModifierSnapshot | None,
    depth: int,
    effect_chain: tuple[str, ...],
    in_damage_results: bool = False,
    require_all_selections: bool = True,
) -> tuple[tuple[PreparedAftermathInstruction, ...], int]:
    if depth < 0 or depth > 16:
        raise PreventionAftermathError("Prevention aftermath damage nesting exceeds its supported bound")
    if not values:
        if selections and require_all_selections:
            raise PreventionAftermathError("Replacement selections were supplied without aftermath damage")
        return (), 0
    if modifier_snapshot is None:
        raise PreventionAftermathError(
            "Damage prevention aftermath requires projected modifier state"
        )
    instructions: list[PreparedAftermathInstruction] = []
    consumed = 0
    remaining = tuple(selections)
    projected = modifier_snapshot
    for index, (application, aftermath_index, aftermath, amount) in enumerate(values):
        if application.effect_id in effect_chain or depth >= 16:
            raise PreventionAftermathError(
                "Prevention aftermath damage cycle is not representable"
            )
        if aftermath.recipient.kind == "prevented_source_controller":
            if len(application.damage_source_controllers) != 1:
                raise PreventionAftermathError(
                    "Prevention aftermath lost the prevented source controller"
                )
            recipient_ref = application.damage_source_controllers[0]
        else:
            assert aftermath.recipient.subject is not None
            recipient_ref = aftermath.recipient.subject.ref
        try:
            recipient = damage_port.recipient(
                recipient_ref,
                actor=aftermath.source.controller,
            )
        except ValueError as exc:
            raise PreventionAftermathError(str(exc)) from exc
        fixed = aftermath.recipient.subject
        if fixed is not None and fixed.kind == "permanent" and (
            recipient.object_id != fixed.object_id
            or recipient.logical_object_id != fixed.logical_object_id
        ):
            raise PreventionAftermathError(
                "Prevention aftermath target changed object identity"
            )
        try:
            nested = damage_port.prepare(
                (
                    DamageProposal(
                        proposal_id=(
                            "damage.prevention.aftermath:damage:"
                            f"{application.effect_id}:{aftermath_index}"
                        ),
                        source=aftermath.source,
                        recipient=recipient,
                        amount=amount,
                        combat=False,
                        reason="damage_prevention_aftermath",
                    ),
                ),
                selections=remaining,
                sources=sources,
                source_zones=source_zones,
                modifier_snapshot=projected,
                aftermath_depth=depth + 1,
                aftermath_effect_chain=(
                    *effect_chain,
                    application.effect_id,
                ),
                require_all_selections=not in_damage_results,
            )
        except ReplacementChoiceRequired as exc:
            if in_damage_results:
                raise
            raise PreventionAftermathError(str(exc)) from exc
        except ValueError as exc:
            raise PreventionAftermathError(str(exc)) from exc
        consumed += nested.consumed_selections
        remaining = remaining[nested.consumed_selections:]
        instructions.append(
            PreparedAftermathInstruction(
                effect_id=application.effect_id,
                source_id=application.source_id,
                kind="deal_damage",
                subject=recipient.ref,
                prevented_amount=application.prevented_amount,
                requested_amount=amount,
                damage_event_ids=application.damage_event_ids,
                nested_damage=nested,
            )
        )
        if index + 1 < len(values):
            if in_damage_results:
                damage_port = damage_port.with_prepared_damage(nested)
                projected = damage_port.modifier_snapshot()
            else:
                projected = project_damage_modifier_snapshot(projected, nested.modifier_plan)
                damage_port = damage_port.with_counter_prevention(nested.aftermath.counter_removals)
    if remaining and require_all_selections:
        raise PreventionAftermathError(
            "Replacement selections were supplied without aftermath damage"
        )
    return tuple(instructions), consumed


def prepare_prevention_aftermath(
    host: PreventionAftermathHost,
    events: Sequence[ReplaceableEvent],
    *,
    damage_port: DamageTransactionPort,
    effects: Sequence[ReplacementEffect] = (),
    selections: Sequence[str | None] = (),
    sources: Sequence[object] | None = None,
    source_zones: Mapping[str, str] | None = None,
    modifier_snapshot: DamageModifierSnapshot | None = None,
    depth: int = 0,
    effect_chain: tuple[str, ...] = (),
) -> PreparedPreventionAftermath:
    if depth < 0 or depth > 16:
        raise PreventionAftermathError(
            "Prevention aftermath damage nesting exceeds its supported bound"
        )
    requests = prevention_aftermath_requests(host, events, effects=effects)
    instructions = list(requests.instructions)
    if requests.damage:
        if requests.life or requests.counters:
            raise PreventionAftermathError(
                "Mixed damage and non-damage prevention aftermath is not yet exact"
            )
        nested, nested_consumed = _prepare_damage_aftermath(
            damage_port,
            requests.damage,
            selections=selections,
            sources=sources,
            source_zones=source_zones,
            modifier_snapshot=modifier_snapshot,
            depth=depth,
            effect_chain=effect_chain,
        )
        instructions.extend(nested)
        return PreparedPreventionAftermath(
            applications=requests.applications,
            instructions=tuple(instructions),
            consumed_selections=nested_consumed,
        )

    life_effects = collect_life_change_replacement_effects(
        host,
        sources=sources,
        source_zones=source_zones,
    )
    try:
        life_batch = prepare_life_change_batch(
            host,
            requests.life,
            effects=life_effects,
            gain_permissions=collect_life_gain_permissions(host,sources=sources,source_zones=source_zones),
            selections=selections,
            require_all_selections=False,
            batch_id=(
                f"replacement:damage.prevention.aftermath.life:"
                f"{host.state.revision}:{host.state.event_sequence + 1}"
            ),
        )
    except LifeChangeError as exc:
        raise PreventionAftermathError(str(exc)) from exc
    if life_batch.pending is not None:
        raise ReplacementChoiceRequired(
            batch=ReplacementEventBatch(
                batch_id=life_batch.batch_id,
                events=life_batch.events,
                apnap_order=tuple(host.apnap_order()),
                journal=life_batch.journal,
            ),
            effects=life_batch.effects,
            pending=life_batch.pending,
        )
    counter_selections = tuple(
        selections[life_batch.consumed_selections:]
    )
    try:
        prepared_counters = prepare_counter_placements(
            host,
            requests.counters,
            selections=counter_selections,
            sources=sources,
            source_zones=source_zones,
        )
        counter_plan = (
            plan_prepared_counter_placement_commit(host, prepared_counters)
            if prepared_counters.events
            else None
        )
    except CounterPlacementError as exc:
        raise PreventionAftermathError(str(exc)) from exc
    if counter_selections and not requests.counters:
        raise PreventionAftermathError(
            "Replacement selections were supplied without counter aftermath"
        )
    return PreparedPreventionAftermath(
        applications=requests.applications,
        instructions=tuple(instructions),
        life_batch=life_batch,
        counter_plan=counter_plan,
        consumed_selections=len(selections),
    )


def prepare_combined_prevention_aftermath(
    requests: PreventionAftermathRequests,
    *,
    damage_port: DamageTransactionPort,
    selections: Sequence[str | None | Mapping[str, Any]],
    sources: Sequence[object] | None,
    source_zones: Mapping[str, str] | None,
    modifier_snapshot: DamageModifierSnapshot,
    depth: int,
    effect_chain: tuple[str, ...],
    require_all_selections: bool,
) -> PreparedPreventionAftermath:
    """Prepare reflected damage after the containing result without changing live state."""
    nested, consumed = _prepare_damage_aftermath(
        damage_port, requests.damage, selections=selections, sources=sources,
        source_zones=source_zones, modifier_snapshot=modifier_snapshot, depth=depth,
        effect_chain=effect_chain, in_damage_results=True,
        require_all_selections=require_all_selections,
    )
    return PreparedPreventionAftermath(
        applications=requests.applications,
        instructions=(*requests.instructions, *nested),
        consumed_selections=consumed,
        in_damage_results=True,
    )


def _linked_prevention_records(
    prepared: PreparedPreventionAftermath,
    results: Sequence[DamageResultRecord],
) -> dict[str, DamageResultRecord]:
    records = {row.event_id: row for row in results if row.cause == "damage_prevention_aftermath"}
    expected = set()
    for instruction in prepared.instructions:
        if instruction.kind == "deal_damage":
            if instruction.nested_damage is None:
                raise PreventionAftermathError("Prepared prevention damage aftermath lost its transaction")
            continue
        record = records.get(instruction.result_event_id)
        if (
            record is None or record.source != instruction.source_id
            or record.requested_amount != instruction.requested_amount
            or instruction.kind == "gain_life" and (
                record.kind != "life.change" or record.player != instruction.subject
            )
            or instruction.kind == "place_counters" and record.kind != "counter.place"
        ):
            raise PreventionAftermathError("Prepared prevention result changed its attribution")
        expected.add(record.event_id)
    if expected != records.keys():
        raise PreventionAftermathError("Prepared damage contains unbound prevention results")
    return records


def validate_prevention_aftermath(
    host: PreventionAftermathHost,
    prepared: PreparedPreventionAftermath,
    *,
    result_records: Sequence[DamageResultRecord] = (),
) -> None:
    if not isinstance(prepared, PreparedPreventionAftermath):
        raise PreventionAftermathError(
            "Prevention aftermath requires a typed prepared value"
        )
    if prepared.in_damage_results:
        _linked_prevention_records(prepared, result_records)
        return
    validate_nested_counter_prevention(host, prepared)
    projected_host = projected_counter_prevention_host(host, prepared.counter_removals)
    if prepared.life_batch is not None:
        try:
            validate_life_change_batch(projected_host, prepared.life_batch)
        except LifeChangeError as exc:
            raise PreventionAftermathError(str(exc)) from exc
    if prepared.counter_plan is not None:
        try:
            validate_counter_placement_commit(projected_host, prepared.counter_plan)
        except CounterPlacementError as exc:
            raise PreventionAftermathError(str(exc)) from exc
    for instruction in prepared.instructions:
        if instruction.kind == "deal_damage" and instruction.nested_damage is None:
            raise PreventionAftermathError(
                "Prepared prevention damage aftermath lost its transaction"
            )


def commit_prevention_aftermath(
    host: PreventionAftermathHost,
    prepared: PreparedPreventionAftermath,
    *,
    damage_port: DamageTransactionPort,
    result_records: Sequence[DamageResultRecord] = (),
) -> PreventionAftermathResult:
    """Commit CR 615.5 results immediately after the prevention batch."""

    if prepared.life_batch is not None:
        try:
            prepared = replace(
                prepared,
                life_batch=replan_life_change_batch(
                    host, prepared.life_batch
                ),
            )
        except LifeChangeError as exc:
            raise PreventionAftermathError(str(exc)) from exc
    validate_prevention_aftermath(host, prepared, result_records=result_records)
    combined_records = _linked_prevention_records(prepared, result_records) if prepared.in_damage_results else None
    removed_counters = commit_counter_prevention_results(host, prepared.counter_removals)
    counter_results = ()
    if prepared.counter_plan is not None:
        try:
            counter_results = commit_counter_placement_plan(
                host,
                prepared.counter_plan,
                reason="damage prevention aftermath",
                dispatch_events=False,
            )
        except CounterPlacementError as exc:
            raise PreventionAftermathError(str(exc)) from exc
    try:
        life_commit = (
            commit_life_change_batch(host, prepared.life_batch)
            if prepared.life_batch is not None
            else None
        )
    except LifeChangeError as exc:
        raise PreventionAftermathError(str(exc)) from exc

    counter_index = 0
    life_records = {
        record.event_id: record
        for record in (() if life_commit is None else life_commit.records)
    }
    events: list[PreventionAftermathEvent] = []
    nested_damage_results: list[DamageTransactionResult] = []
    nested_changed_players: list[str] = []
    nested_changed_objects: list[str] = []
    for instruction in prepared.instructions:
        applied = instruction.requested_amount
        if combined_records is not None and instruction.kind != "deal_damage":
            record = combined_records[instruction.result_event_id]
            applied = 0 if record.direction == "loss" else record.amount
        elif instruction.kind == "gain_life":
            record = life_records.get(str(instruction.result_event_id or ""))
            if record is None:
                raise PreventionAftermathError(
                    "Prevention life aftermath lost its resolved event"
                )
            applied = record.amount
        elif instruction.kind == "place_counters":
            applied = counter_results[counter_index].placed
            counter_index += 1
        if instruction.kind == "deal_damage":
            try:
                assert instruction.nested_damage is not None
                nested_result = damage_port.commit(instruction.nested_damage)
            except ValueError as exc:
                raise PreventionAftermathError(str(exc)) from exc
            nested_damage_results.append(nested_result)
            nested_changed_players.extend(nested_result.changed_players)
            nested_changed_objects.extend(nested_result.changed_objects)
            applied = nested_result.dealt_amount
        events.append(
            PreventionAftermathEvent(
                effect_id=instruction.effect_id,
                source_id=instruction.source_id,
                kind=instruction.kind,
                subject=instruction.subject,
                prevented_amount=instruction.prevented_amount,
                applied_amount=applied,
                damage_event_ids=instruction.damage_event_ids,
            )
        )
    from .counter_placement_events import capture_counter_damage_occurrences
    counter_occurrences=() if prepared.counter_plan is None else capture_counter_damage_occurrences(
        host,prepared.counter_plan.prepared.events,reason="damage prevention aftermath")
    return PreventionAftermathResult(
        events=tuple(events),
        changed_players=tuple(
            sorted(
                (() if life_commit is None else life_commit.changed_players)
                + tuple(nested_changed_players)
            )
        ),
        changed_objects=tuple(
            sorted(
                {result.object_id for result in counter_results}.union(
                    nested_changed_objects
                ).union(
                    result.object_id for result in removed_counters if result.removed
                )
            )
        ),
        nested_damage_results=tuple(nested_damage_results),
        counter_occurrences=counter_occurrences,
    )


__all__ = [
    "PreparedPreventionAftermath",
    "PreventionAftermathError",
    "PreventionAftermathEvent",
    "PreventionAftermathResult",
    "commit_prevention_aftermath",
    "prepare_prevention_aftermath",
    "prevention_applications",
    "validate_prevention_aftermath",
]
