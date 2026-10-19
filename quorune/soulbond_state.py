"""Narrow mutation owner for retained, symmetric Soulbond relationships."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Protocol

from .continuous_effect_model import ContinuousObjectIdentity, Layer
from .control_effects import source_continuity_snapshot
from .source_continuity import SourceContinuitySnapshot
from .soulbond_model import (
    SoulbondError, SoulbondObjectSnapshot, SoulbondPair, SoulbondOpportunity,
    canonical_pair_book, current_pairs, propose_pair,
)


class SoulbondStateHost(Protocol):
    state: Any
    active_seats: list[str]

    def _effective_card_data(self, card: Any, *, maximum_layer: Layer) -> dict[str, Any]: ...
    def _type_parts(self, type_line: str) -> tuple[set[str], set[str], set[str]]: ...
    def _log(self, *args: Any, **kwargs: Any) -> Any: ...


def retained_pairs(state: Any) -> tuple[SoulbondPair, ...]:
    return canonical_pair_book(getattr(state, 'soulbond_pairs', None) or ())


def capture_pairing_objects(host: SoulbondStateHost) -> tuple[SoulbondObjectSnapshot, ...]:
    tracked = {member.identity.object_id for pair in retained_pairs(host.state)
               for member in (pair.first, pair.second)}
    rows = []
    for card in host.state.cards.values():
        battlefield = card.zone == 'battlefield'
        if not battlefield and card.object_id not in tracked:
            continue
        # Pair-granted abilities and P/T modifiers do not supply layer-4 types.
        data = host._effective_card_data(card, maximum_layer=Layer.TYPE) if battlefield else {}
        rows.append(SoulbondObjectSnapshot(
            ContinuousObjectIdentity(card.object_id, card.logical_object_id),
            card.ref, card.controller or card.owner, battlefield,
            'creature' in host._type_parts(str(data.get('type_line') or ''))[0],
            phased_out=card.phased_out,
            history=card.source_continuity.snapshot() if card.source_continuity is not None else None,
        ))
    return tuple(rows)


def commit_soulbond_pair(host: SoulbondStateHost, *, pair_id: str, source_ref: str,
                         partner_ref: str, controller: str, entered_ref: str | None = None,
                         source_identity: str | None = None, partner_identity: str | None = None,
                         entered_identity: str | None = None) -> SoulbondPair:
    if controller not in host.active_seats:
        raise SoulbondError('The pairing controller must be an active player')
    before = retained_pairs(host.state)
    if any(pair.pair_id == pair_id for pair in before):
        raise SoulbondError('A retained pair identity cannot be reused')
    objects = capture_pairing_objects(host)
    for ref, expected in ((source_ref, source_identity), (partner_ref, partner_identity),
                          (entered_ref, entered_identity)):
        if expected is not None and not any(row.ref == ref and row.identity.logical_object_id == expected for row in objects):
            raise SoulbondError('A selected Soulbond incarnation is no longer present')
    active = current_pairs(before, objects)
    # Validate every proposal before initializing continuity or changing state.
    known = tuple(replace(row, history=row.history or SourceContinuitySnapshot(0, 0, 0)) for row in objects)
    pair = propose_pair(pair_id=pair_id, source_ref=source_ref, partner_ref=partner_ref,
        controller=controller, objects=known, pairs=active, entered_ref=entered_ref)
    for member in (pair.first, pair.second):
        captured = source_continuity_snapshot(host.state.cards[member.identity.object_id])
        if captured != member.history:
            raise SoulbondError('Pairing continuity changed during the atomic proposal')
    host.state.soulbond_pairs = canonical_pair_book((*active, pair))
    host._log(controller, 'creature.paired', 'Two creatures became paired.',
        {'objects':[source_ref, partner_ref]}, importance=1,
        changed_objects=[pair.first.identity.object_id, pair.second.identity.object_id])
    return pair


def opportunity_candidates(host: SoulbondStateHost, opportunity: SoulbondOpportunity) -> tuple[str, ...]:
    if not isinstance(opportunity, SoulbondOpportunity):
        raise SoulbondError('A Soulbond opportunity must be typed')
    return opportunity.candidates(capture_pairing_objects(host), retained_pairs(host.state))


def intervening_pair_available(host: SoulbondStateHost, source: Any, context: Any, *, other_entry: bool) -> bool:
    identity = context.get('source_logical_object_id', source.logical_object_id)
    if source.logical_object_id != identity:
        return False
    controller = context.get('intervening_condition_controller', source.controller)
    entered_ref = context.get('card') if other_entry else None
    entered_identity = context.get('card_object_identity') if other_entry else None
    if other_entry and (not entered_ref or not entered_identity):
        return False
    opportunity = SoulbondOpportunity(controller, source.ref, identity,
        'other_entry' if other_entry else 'source_entry', entered_ref, entered_identity)
    return bool(opportunity_candidates(host, opportunity))


def paired_object_identity(state: Any, source: Any) -> ContinuousObjectIdentity | None:
    """Read the reconciled relationship without recursing into type layers.

    Type changes are reconciled by the mutation boundary. Identity, control,
    phasing and retained continuity still guard every static lowering.
    """
    identity = ContinuousObjectIdentity(source.object_id, source.logical_object_id)
    for pair in retained_pairs(state):
        partner = pair.partner_of(identity)
        if partner is None:
            continue
        for member in (pair.first, pair.second):
            card = state.cards.get(member.identity.object_id)
            history = card.source_continuity if card is not None else None
            if (card is None or card.logical_object_id != member.identity.logical_object_id
                    or card.zone != 'battlefield' or card.phased_out or card.controller != pair.controller
                    or history is None or history.control_epoch != member.history.control_epoch
                    or history.phase_epoch != member.history.phase_epoch):
                break
        else:
            return partner
    return None


def reconcile_soulbond_pairs(host: SoulbondStateHost) -> bool:
    before = retained_pairs(host.state)
    if not before:
        return False
    objects = capture_pairing_objects(host)
    active = current_pairs(before, objects)
    if active == before:
        return False
    host.state.soulbond_pairs = active
    kept = {pair.pair_id for pair in active}
    expired_ids = {member.identity.object_id for pair in before if pair.pair_id not in kept
                   for member in (pair.first, pair.second)}
    visible = [row.ref for row in objects if row.on_battlefield and not row.phased_out
               and row.identity.object_id in expired_ids]
    host._log(None, 'creature.unpaired', 'A creature pair ended.',
        {'objects':sorted(visible), 'pairs_ended':len(before) - len(active)}, importance=1,
        changed_objects=sorted(expired_ids))
    return True


def end_pairs_containing(host: SoulbondStateHost, object_ids, *, reason: str) -> bool:
    """End relationships on a committed zone/control/phase transition."""
    affected = frozenset(object_ids)
    before = retained_pairs(host.state)
    active = tuple(pair for pair in before
                   if not any(member.identity.object_id in affected for member in (pair.first, pair.second)))
    if active == before:
        return False
    host.state.soulbond_pairs = active
    visible = sorted(card.ref for card in host.state.cards.values()
                     if card.object_id in affected and card.zone == 'battlefield' and not card.phased_out)
    host._log(None, 'creature.unpaired', 'A creature pair ended.',
        {'objects':visible, 'reason':reason, 'pairs_ended':len(before) - len(active)}, importance=1)
    return True
