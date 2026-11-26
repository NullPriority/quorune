from __future__ import annotations

"""Resolution ownership for one immutable public tap-state set."""

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from .affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    select_affected_permanents,
    target_player_creature_snapshot_spec,
    target_unspent_mana_tap_spec,
)
from .mana_provenance import lose_all_unspent_mana
from .mana_undo import clear_mana_undo_stack
from .object_query import ObjectQueryResult
from .continuous_effect_model import ContinuousObjectIdentity
from .source_untap_rule_model import PendingSourceUntapRestraint
from .tap_state import dispatch_tap_state_occurrence, prohibit_next_player_untap, set_permanent_tapped
from .trigger_processing import enqueue_trigger_batch


class PublicTapStateSetError(ValueError):
    pass


def _validate_snapshot_restraint(flag, spec, tapped, untap_restraint, lose_unspent_mana):
    if type(flag) is not bool or flag and (
        not tapped or untap_restraint is not None or lose_unspent_mana
        or spec.target_controller is None or spec != target_player_creature_snapshot_spec(spec.target_controller)
    ):
        raise PublicTapStateSetError("The next-player untap restriction requires the exact original creature set")


@dataclass(frozen=True, slots=True)
class SetPublicPermanentsTappedIntent:
    actor: str
    spec: AffectedPermanentSetSpec
    tapped: bool
    reason: str
    source_ref: str | None = None
    untap_restraint: PendingSourceUntapRestraint | None = None
    lose_unspent_mana: bool = False
    restrain_snapshot_on_player_untap: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.actor) is not str
            or not self.actor
            or not isinstance(self.spec, AffectedPermanentSetSpec)
            or type(self.tapped) is not bool
            or type(self.reason) is not str
            or not self.reason
            or (
                self.source_ref is not None
                and (type(self.source_ref) is not str or not self.source_ref)
            )
            or (self.spec.exclude_source and self.source_ref is None and self.untap_restraint is None)
            or self.untap_restraint is not None and (
                not self.tapped or not isinstance(self.untap_restraint, PendingSourceUntapRestraint)
            )
            or type(self.lose_unspent_mana) is not bool
            or self.lose_unspent_mana and (
                not self.tapped or self.untap_restraint is not None or self.spec.target_controller is None
                or self.spec != target_unspent_mana_tap_spec(self.spec.target_controller)
            )
        ):
            raise ValueError("Public-set tap-state intent is malformed")
        _validate_snapshot_restraint(self.restrain_snapshot_on_player_untap, self.spec, self.tapped,
                                     self.untap_restraint, self.lose_unspent_mana)


@dataclass(frozen=True, slots=True)
class PublicTapStatePermanent:
    object_id: str
    logical_object_id: str
    ref: str
    controller: str


class PublicTapStateSetHost(Protocol):
    state: Any

    def affected_permanent_active_seats(self) -> tuple[str, ...]: ...

    def affected_permanent_apnap_order(self) -> tuple[str, ...]: ...

    def affected_permanent_object_rows(
        self, actor: str
    ) -> tuple[ObjectQueryResult, ...]: ...

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


def snapshot_public_tap_state(rows) -> tuple[PublicTapStatePermanent, ...]:
    """Capture stable public identity for each selected permanent."""
    return tuple(
        PublicTapStatePermanent(
            object_id=row.object_id,
            logical_object_id=row.logical_object_id,
            ref=row.ref,
            controller=row.controller,
        )
        for row in rows
    )


def commit_public_tap_state_snapshot(
    host: PublicTapStateSetHost,
    snapshot: tuple[PublicTapStatePermanent, ...],
    *,
    actor: str,
    tapped: bool,
    reason: str,
) -> tuple[str, ...]:
    """Mutate the selected incarnations as a group before publishing tap events."""
    for value in snapshot:
        card = host.state.cards.get(value.object_id)
        if (
            card is None
            or card.zone != "battlefield"
            or card.logical_object_id != value.logical_object_id
            or card.ref != value.ref
            or card.controller != value.controller
            or card.phased_out
        ):
            raise PublicTapStateSetError(
                "Public tap-state set became stale before commit"
            )
    prior_states = {value.object_id: host.state.cards[value.object_id].tapped for value in snapshot}
    results = tuple(
        set_permanent_tapped(
            host,
            value.ref,
            actor=actor,
            tapped=tapped,
            reason=reason,
            logical_object_id=value.logical_object_id,
            semantic_events=False,
        )
        for value in snapshot
    )
    pending: list[Any] = []
    for value in snapshot:
        card = host.state.cards[value.object_id]
        if card.tapped != prior_states[value.object_id]:
            dispatch_tap_state_occurrence(
                host, card, tapped=tapped, reason=reason, trigger_batch=pending,
            )
    if pending:
        enqueue_trigger_batch(host, pending)
    return results


def resolve_public_tap_state_set(
    host: PublicTapStateSetHost,
    *,
    actor: str,
    spec: AffectedPermanentSetSpec,
    tapped: bool,
    reason: str,
    source_ref: str | None = None,
    untap_restraint: PendingSourceUntapRestraint | None = None,
    lose_unspent_mana: bool = False,
    restrain_snapshot_on_player_untap: bool = False,
) -> tuple[str, ...]:
    if type(actor) is not str or not actor or type(reason) is not str or not reason:
        raise PublicTapStateSetError(
            "Public tap-state sets require actor and reason"
        )
    if type(tapped) is not bool:
        raise PublicTapStateSetError("Public tap-state result must be boolean")
    if untap_restraint is not None and (not tapped or not isinstance(untap_restraint, PendingSourceUntapRestraint)):
        raise PublicTapStateSetError("Public untap restraint requires a typed tap instruction")
    _validate_snapshot_restraint(restrain_snapshot_on_player_untap, spec, tapped, untap_restraint, lose_unspent_mana)
    rows = host.affected_permanent_object_rows(actor)
    if untap_restraint is not None and spec.exclude_source:
        original = untap_restraint.source
        if original is not None:
            rows = tuple(row for row in rows if (row.object_id, row.logical_object_id) != (
                original.object_id, original.logical_object_id,
            ))
        spec = replace(spec, exclude_source=False)
    try:
        selected = select_affected_permanents(
            rows,
            spec,
            actor=actor,
            active_seats=host.affected_permanent_active_seats(),
            apnap_order=host.affected_permanent_apnap_order(),
            source_ref=source_ref,
        )
    except AffectedPermanentSetError as exc:
        raise PublicTapStateSetError(str(exc)) from exc
    snapshot = snapshot_public_tap_state(selected)
    results = commit_public_tap_state_snapshot(host, snapshot, actor=actor, tapped=tapped, reason=reason)
    if restrain_snapshot_on_player_untap:
        for value in snapshot:
            prohibit_next_player_untap(host, host.state.cards[value.object_id], actor=spec.target_controller)
    if untap_restraint is not None:
        from .rules.untap_permissions import commit_source_untap_restraint
        commit_source_untap_restraint(host, untap_restraint, tuple(
            ContinuousObjectIdentity(value.object_id, value.logical_object_id) for value in snapshot
        ))
    host._log(
        actor,
        "effect.permanent.tap_state_set",
        f"Processed {len(snapshot)} permanent tap state(s) from a fixed set.",
        {
            "affected_count": len(snapshot),
            "tapped": tapped,
            "reason": reason,
        },
        importance=2,
        changed_objects=tuple(value.object_id for value in snapshot),
        changed_players=tuple(sorted({value.controller for value in snapshot})),
    )
    if lose_unspent_mana:
        player = host.state.players[spec.target_controller]
        clear_mana_undo_stack(player.stats)
        lost = lose_all_unspent_mana(player)
        if lost:
            host._log(
                spec.target_controller, "mana.empty", f"{spec.target_controller} lost their unspent mana.",
                {"lost": lost, "reason": reason}, importance=1, changed_players=[spec.target_controller],
            )
    return results


__all__ = [
    "PublicTapStateSetError",
    "SetPublicPermanentsTappedIntent",
    "commit_public_tap_state_snapshot",
    "resolve_public_tap_state_set",
    "snapshot_public_tap_state",
]
