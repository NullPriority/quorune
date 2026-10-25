from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence
from .life_gain_permission import ALLOW_ALL_LIFE_GAIN, LifeGainPermissionSnapshot

from .life_state import (
    apply_life_changes,
    LifeChange,
    LifeStateHost,
    LifeStateView,
    LifeStateError,
    LifeStatePlan,
    plan_life_changes,
    validate_life_changes,
)
from .replacement import (
    ReplaceableEvent,
    ReplacementBatchChoice,
    ReplacementEffect,
    ReplacementEffectError,
    ReplacementEventBatch,
    ReplacementSelection,
    advance_replacement_batch,
    resolve_replacement_batch,
)


class LifeChangeError(ValueError):
    """A replacement-capable life-change batch is malformed or stale."""


_LOSS_DIRECTION = "".join(("lo", "ss"))


class LifeChangeState(LifeStateView, Protocol):
    revision: int
    event_sequence: int


class LifeChangeHost(LifeStateHost, Protocol):
    state: LifeChangeState

    def apnap_order(self, *, start: str | None = None) -> list[str]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_players: Sequence[str] = (),
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class LifeChangeRequest:
    event_id: str
    player: str
    amount: int
    source: str | None = None
    source_controller: str | None = None
    cause: str = "effect"

    def __post_init__(self) -> None:
        if not str(self.event_id or "") or not str(self.player or ""):
            raise LifeChangeError(
                "Life-change requests require stable event and player IDs"
            )
        if type(self.amount) is not int:
            raise LifeChangeError("Life-change request amounts must be integers")
        if not str(self.cause or ""):
            raise LifeChangeError("Life-change requests require a cause")
        for value in (self.source, self.source_controller):
            if value == "":
                raise LifeChangeError(
                    "Life-change source identities cannot be empty"
                )


@dataclass(frozen=True, slots=True)
class LifeChangeRecord:
    event_id: str
    player: str
    direction: str
    requested_amount: int
    amount: int
    source: str | None
    source_controller: str | None
    cause: str

    @property
    def delta(self) -> int:
        return self.amount if self.direction == "gain" else -self.amount


@dataclass(frozen=True, slots=True)
class PreparedLifeChangeBatch:
    batch_id: str
    requested_events: tuple[ReplaceableEvent, ...]
    events: tuple[ReplaceableEvent, ...]
    effects: tuple[ReplacementEffect, ...]
    journal: tuple[ReplacementSelection, ...]
    plan: LifeStatePlan
    records: tuple[LifeChangeRecord, ...]
    pending: ReplacementBatchChoice | None = None
    consumed_selections: int = 0
    gain_permissions: LifeGainPermissionSnapshot = ALLOW_ALL_LIFE_GAIN


@dataclass(frozen=True, slots=True)
class LifeChangeCommit:
    records: tuple[LifeChangeRecord, ...]
    changed_players: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LifePlayerResult:
    player: str
    requested_gain: int = 0
    requested_loss: int = 0
    resolved_gain: int = 0
    resolved_loss: int = 0

    @property
    def requested_delta(self) -> int:
        return self.requested_gain - self.requested_loss

    @property
    def delta(self) -> int:
        return self.resolved_gain - self.resolved_loss

    def to_dict(self) -> dict[str, Any]:
        return {
            "player": self.player,
            "requested_gain": self.requested_gain,
            "requested_loss": self.requested_loss,
            "requested_delta": self.requested_delta,
            "resolved_gain": self.resolved_gain,
            "resolved_loss": self.resolved_loss,
            "delta": self.delta,
        }


@dataclass(frozen=True, slots=True)
class LifeBatchResult:
    batch_id: str
    players: tuple[LifePlayerResult, ...]
    events: tuple[LifeChangeRecord, ...]
    replacement_journal: tuple[ReplacementSelection, ...]

    def for_player(self, player: str) -> LifePlayerResult:
        return next(
            (result for result in self.players if result.player == player),
            LifePlayerResult(player=player),
        )

    @property
    def changed_players(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                result.player for result in self.players if result.delta != 0
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "life_batch": self.batch_id,
            "life_players": [result.to_dict() for result in self.players],
            "life_events": [
                {
                    "event_id": record.event_id,
                    "player": record.player,
                    "direction": record.direction,
                    "requested_amount": record.requested_amount,
                    "amount": record.amount,
                    "source": record.source,
                    "source_controller": record.source_controller,
                    "cause": record.cause,
                }
                for record in self.events
            ],
            "replacement_journal": [
                selection.to_dict() for selection in self.replacement_journal
            ],
        }


def _event(request: LifeChangeRequest) -> ReplaceableEvent:
    direction = "gain" if request.amount >= 0 else _LOSS_DIRECTION
    amount = abs(request.amount)
    return ReplaceableEvent(
        event_id=request.event_id,
        kind="life.change",
        affected_player=request.player,
        payload={
            "player": request.player,
            "direction": direction,
            "amount": amount,
            "requested_amount": amount,
            "source": request.source,
            "source_controller": request.source_controller,
            "cause": request.cause,
        },
    )


def _record(event: ReplaceableEvent) -> LifeChangeRecord:
    if event.kind != "life.change" or event.affected_player is None:
        raise LifeChangeError("Resolved life changes must affect one player")
    if event.children:
        raise LifeChangeError("Resolved life changes cannot contain events")
    player = str(event.payload.get("player") or "")
    if player != event.affected_player:
        raise LifeChangeError("Resolved life change has the wrong player")
    direction = str(event.payload.get("direction") or "")
    if direction not in {"gain", _LOSS_DIRECTION}:
        raise LifeChangeError("Resolved life change requires gain or loss")
    amount = event.payload.get("amount")
    requested = event.payload.get("requested_amount")
    if (
        type(amount) is not int
        or amount < 0
        or type(requested) is not int
        or requested < 0
    ):
        raise LifeChangeError(
            "Resolved life-change amounts must be nonnegative integers"
        )
    return LifeChangeRecord(
        event_id=event.event_id,
        player=player,
        direction=direction,
        requested_amount=requested,
        amount=amount,
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
        cause=str(event.payload.get("cause") or "effect"),
    )


def _state_plan(
    host: LifeChangeHost,
    records: Sequence[LifeChangeRecord],
) -> LifeStatePlan:
    try:
        return plan_life_changes(
            host,
            tuple(
                LifeChange(player=record.player, amount=record.delta)
                for record in records
            ),
        )
    except LifeStateError as exc:
        raise LifeChangeError(str(exc)) from exc


def summarize_life_change_batch(
    prepared: PreparedLifeChangeBatch,
) -> LifeBatchResult:
    """Return canonical requested and final values for public audit logs."""

    if not isinstance(prepared, PreparedLifeChangeBatch):
        raise LifeChangeError("Life summaries require a typed prepared batch")
    if prepared.pending is not None:
        raise LifeChangeError(
            "Life summaries cannot be built with a pending replacement choice"
        )
    order: list[str] = []
    values: dict[str, dict[str, int]] = {}

    def amounts(player: str) -> dict[str, int]:
        if player not in values:
            order.append(player)
            values[player] = {
                "requested_gain": 0,
                "requested_loss": 0,
                "resolved_gain": 0,
                "resolved_loss": 0,
            }
        return values[player]

    for event in prepared.requested_events:
        if event.kind != "life.change" or event.affected_player is None:
            raise LifeChangeError("Requested life summary event is malformed")
        direction = str(event.payload.get("direction") or "")
        requested = event.payload.get("requested_amount")
        if direction not in {"gain", _LOSS_DIRECTION} or (
            type(requested) is not int or requested < 0
        ):
            raise LifeChangeError("Requested life summary amount is malformed")
        amounts(str(event.affected_player))[
            f"requested_{direction}"
        ] += requested
    for record in prepared.records:
        amounts(record.player)[f"resolved_{record.direction}"] += record.amount
    return LifeBatchResult(
        batch_id=prepared.batch_id,
        players=tuple(
            LifePlayerResult(player=player, **values[player])
            for player in order
        ),
        events=prepared.records,
        replacement_journal=prepared.journal,
    )


def _permission_events(events, permissions):
    if not isinstance(permissions, LifeGainPermissionSnapshot):
        raise LifeChangeError('Life changes require a typed gain-permission snapshot')
    allowed, suppressed = [], []
    for event in events:
        if event.payload.get('direction') == 'gain' and not permissions.allows_gain(event.affected_player):
            suppressed.append(replace(event, payload={**event.payload, 'amount': 0}))
        else:
            allowed.append(event)
    return tuple(allowed), tuple(suppressed)


def prepare_life_change_batch(
    host: LifeChangeHost,
    requests: Sequence[LifeChangeRequest],
    *,
    effects: Sequence[ReplacementEffect] = (),
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    require_all_selections: bool = True,
    batch_id: str | None = None,
    gain_permissions: LifeGainPermissionSnapshot = ALLOW_ALL_LIFE_GAIN,
) -> PreparedLifeChangeBatch:
    """Resolve one simultaneous life batch without mutating state."""

    nonzero = tuple(request for request in requests if request.amount != 0)
    requested_events = tuple(_event(request) for request in nonzero)
    event_ids = tuple(event.event_id for event in requested_events)
    if len(event_ids) != len(set(event_ids)):
        raise LifeChangeError("Life-change event IDs must be unique")
    allowed_events, suppressed_events = _permission_events(requested_events, gain_permissions)
    resolved_events = allowed_events
    journal: tuple[ReplacementSelection, ...] = ()
    pending: ReplacementBatchChoice | None = None
    consumed = 0
    stable_batch_id = batch_id or (
        f"replacement:life.change:{host.state.revision}:"
        f"{host.state.event_sequence + 1}"
    )
    if not str(stable_batch_id or ""):
        raise LifeChangeError("Life-change batches require a stable ID")
    if allowed_events and effects:
        try:
            progress = advance_replacement_batch(
                ReplacementEventBatch(
                    batch_id=stable_batch_id,
                    events=allowed_events,
                    apnap_order=tuple(host.apnap_order()),
                ),
                effects,
                selections=selections,
                require_all_selections=require_all_selections,
            )
        except ReplacementEffectError as exc:
            raise LifeChangeError(str(exc)) from exc
        resolved_events = progress.batch.events
        journal = progress.batch.journal
        pending = progress.pending
        consumed = progress.consumed_selections
    elif selections and require_all_selections:
        raise LifeChangeError(
            "Replacement selections were supplied without a life change"
        )
    # Suppressed audit results are not eligible replacement events and must
    # not change a pending choice's containing-batch identity.
    if pending is None:
        resolved_events = (*suppressed_events, *resolved_events)
    records = () if pending is not None else tuple(
        _record(event) for event in resolved_events
    )
    plan = _state_plan(host, records)
    return PreparedLifeChangeBatch(
        batch_id=stable_batch_id,
        requested_events=requested_events,
        events=resolved_events,
        effects=tuple(effects),
        journal=journal,
        plan=plan,
        records=records,
        pending=pending,
        consumed_selections=consumed,
        gain_permissions=gain_permissions,
    )


def validate_life_change_batch(
    host: LifeChangeHost,
    prepared: PreparedLifeChangeBatch,
) -> None:
    if not isinstance(prepared, PreparedLifeChangeBatch):
        raise LifeChangeError("Life commits require a typed prepared batch")
    if not isinstance(prepared.gain_permissions, LifeGainPermissionSnapshot):
        raise LifeChangeError('Life changes require a typed gain-permission snapshot')
    if prepared.pending is not None:
        raise LifeChangeError(
            "Life changes cannot commit with a pending replacement choice"
        )
    if not prepared.requested_events:
        if prepared.events or prepared.journal or prepared.records:
            raise LifeChangeError("An empty life batch contains resolved data")
        try:
            validate_life_changes(host, prepared.plan)
        except LifeStateError as exc:
            raise LifeChangeError(str(exc)) from exc
        return
    try:
        allowed_events, suppressed_events = _permission_events(prepared.requested_events, prepared.gain_permissions)
        if allowed_events:
            replayed = resolve_replacement_batch(
                ReplacementEventBatch(
                    batch_id=prepared.batch_id,
                    events=allowed_events,
                    apnap_order=tuple(host.apnap_order()),
                ),
                prepared.effects,
                selections=prepared.journal,
            )
            expected_events = replayed.events
        else:
            if prepared.journal:
                raise LifeChangeError('A fully prohibited life batch has no replacement journal')
            expected_events = ()
    except ReplacementEffectError as exc:
        raise LifeChangeError(str(exc)) from exc
    if (*suppressed_events, *expected_events) != prepared.events:
        raise LifeChangeError("Life replacement journal changed before commit")
    try:
        validate_life_changes(host, prepared.plan)
    except LifeStateError as exc:
        raise LifeChangeError(str(exc)) from exc


def replan_life_change_batch(
    host: LifeChangeHost,
    prepared: PreparedLifeChangeBatch,
) -> PreparedLifeChangeBatch:
    """Rebase a validated follow-up event after its preceding event commits.

    CR 615.5 aftermath is created before the damage transaction mutates state,
    but happens immediately after that transaction. The immutable replacement
    result remains pinned; only its typed mutation plan is rebased against the
    just-committed life total.
    """

    if not isinstance(prepared, PreparedLifeChangeBatch):
        raise LifeChangeError("Life replanning requires a typed prepared batch")
    if prepared.pending is not None:
        raise LifeChangeError(
            "Life changes cannot be replanned with a pending choice"
        )
    return replace(prepared, plan=_state_plan(host, prepared.records))


def commit_life_change_batch(
    host: LifeChangeHost,
    prepared: PreparedLifeChangeBatch,
    *,
    log_replacements: bool = True,
) -> LifeChangeCommit:
    """Commit one replay-validated life batch through the typed state owner."""

    validate_life_change_batch(host, prepared)
    transitions = apply_life_changes(host, prepared.plan)
    if log_replacements:
        effects = {effect.effect_id: effect for effect in prepared.effects}
        events = {event.event_id: event for event in prepared.events}
        for selection in prepared.journal:
            effect_id = str(selection.effect_id or "")
            if effect_id.startswith("decline:"):
                continue
            effect = effects.get(effect_id)
            event = events.get(selection.event_id)
            if effect is None or event is None:
                raise LifeChangeError(
                    "Life replacement journal does not match its snapshot"
                )
            host._log(
                None,
                "replacement.apply",
                f"{effect.source_id} changed a life change.",
                {
                    "source": effect.source_id,
                    "effect_id": effect.effect_id,
                    "player": event.affected_player,
                    "direction": event.payload.get("direction"),
                    "requested": event.payload.get("requested_amount"),
                    "resolved": event.payload.get("amount"),
                },
                importance=2,
                changed_players=[str(event.affected_player)],
            )
    return LifeChangeCommit(
        records=prepared.records,
        changed_players=tuple(
            sorted(
                transition.player
                for transition in transitions
                if transition.before != transition.after
            )
        ),
    )


__all__ = [
    "commit_life_change_batch",
    "LifeChangeCommit",
    "LifeChangeError",
    "LifeBatchResult",
    "LifePlayerResult",
    "LifeChangeRecord",
    "LifeChangeRequest",
    "PreparedLifeChangeBatch",
    "prepare_life_change_batch",
    "replan_life_change_batch",
    "summarize_life_change_batch",
    "validate_life_change_batch",
]
