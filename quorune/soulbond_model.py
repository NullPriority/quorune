"""Immutable CR 702.95 pair identities, eligibility and continuity checks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Any

from .continuous_effect_model import ContinuousObjectIdentity
from .source_continuity import SourceContinuitySnapshot

SOULBOND_CAPABILITY = 'pairing.soulbond'
SOULBOND_CHOICE_OPERATION = 'offer_soulbond_pair'
SOULBOND_COVERAGE = 'identity_pinned_soulbond_pairing'
SOULBOND_SELF_CONDITION = 'soulbond_self_entry_available'
SOULBOND_OTHER_CONDITION = 'soulbond_other_entry_available'
SOULBOND_GRANT_CAPABILITY = 'continuous.soulbond.paired_grant'
SOULBOND_GRANT_HANDLER = 'continuous.soulbond.paired-grant.v1'


class SoulbondError(ValueError):
    """A pair proposal or retained relationship is malformed."""


def _string(value: Any, field: str) -> None:
    if type(value) is not str or not value:
        raise SoulbondError(f'{field} must be a nonempty exact string')


def _fields(value: Any, expected: set[str], field: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise SoulbondError(f'{field} has missing or unknown fields')


@dataclass(frozen=True, slots=True)
class SoulbondObjectSnapshot:
    identity: ContinuousObjectIdentity
    ref: str
    controller: str
    on_battlefield: bool
    is_creature: bool
    phased_out: bool = False
    history: SourceContinuitySnapshot | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ContinuousObjectIdentity):
            raise SoulbondError('Pairing objects require a typed incarnation')
        _string(self.ref, 'Object reference')
        _string(self.controller, 'Object controller')
        if any(type(getattr(self, field)) is not bool
               for field in ('on_battlefield', 'is_creature', 'phased_out')):
            raise SoulbondError('Pairing object facts must be exact booleans')
        if self.history is not None and not isinstance(self.history, SourceContinuitySnapshot):
            raise SoulbondError('Pairing object continuity must be typed')

    def eligible_for(self, controller: str) -> bool:
        return (self.controller == controller and self.on_battlefield
                and self.is_creature and not self.phased_out)


@dataclass(frozen=True, slots=True)
class SoulbondMember:
    identity: ContinuousObjectIdentity
    history: SourceContinuitySnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ContinuousObjectIdentity) or not isinstance(
            self.history, SourceContinuitySnapshot
        ):
            raise SoulbondError('Pair members require typed identity and continuity')

    def to_dict(self) -> dict[str, Any]:
        return {'identity': self.identity.to_dict(), 'history': self.history.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SoulbondMember:
        _fields(value, {'identity', 'history'}, 'Pair member')
        return cls(ContinuousObjectIdentity.from_dict(value['identity']),
                   SourceContinuitySnapshot.from_dict(value['history']))

    def matches(self, current: SoulbondObjectSnapshot, controller: str) -> bool:
        history = current.history
        return (current.identity == self.identity and current.eligible_for(controller)
                and history is not None
                and history.control_epoch == self.history.control_epoch
                and history.phase_epoch == self.history.phase_epoch)


@dataclass(frozen=True, slots=True)
class SoulbondPair:
    pair_id: str
    controller: str
    first: SoulbondMember
    second: SoulbondMember

    def __post_init__(self) -> None:
        _string(self.pair_id, 'Pair identity')
        _string(self.controller, 'Pair controller')
        if not isinstance(self.first, SoulbondMember) or not isinstance(self.second, SoulbondMember):
            raise SoulbondError('Pair endpoints must be typed members')
        if self.first.identity.object_id == self.second.identity.object_id:
            raise SoulbondError('A creature cannot be paired with itself')

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': 1, 'pair_id': self.pair_id, 'controller': self.controller,
                'first': self.first.to_dict(), 'second': self.second.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SoulbondPair:
        _fields(value, {'schema_version', 'pair_id', 'controller', 'first', 'second'}, 'Pair')
        if type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise SoulbondError('Unsupported pair schema')
        return cls(value['pair_id'], value['controller'],
                   SoulbondMember.from_dict(value['first']), SoulbondMember.from_dict(value['second']))

    def partner_of(self, identity: ContinuousObjectIdentity) -> ContinuousObjectIdentity | None:
        if self.first.identity == identity:
            return self.second.identity
        if self.second.identity == identity:
            return self.first.identity
        return None


def canonical_pair_book(pairs: Iterable[SoulbondPair]) -> tuple[SoulbondPair, ...]:
    values = tuple(pairs)
    if any(not isinstance(pair, SoulbondPair) for pair in values):
        raise SoulbondError('Pair book requires typed relationships')
    identifiers = [pair.pair_id for pair in values]
    members = [member.identity.object_id for pair in values for member in (pair.first, pair.second)]
    if len(identifiers) != len(set(identifiers)) or len(members) != len(set(members)):
        raise SoulbondError('Each pair and each paired object must have one identity')
    return tuple(sorted(values, key=lambda pair: pair.pair_id))


def _object_map(objects: Iterable[SoulbondObjectSnapshot]) -> dict[str, SoulbondObjectSnapshot]:
    values = tuple(objects)
    if any(not isinstance(value, SoulbondObjectSnapshot) for value in values):
        raise SoulbondError('Pair checks require typed current objects')
    identities = [value.identity.object_id for value in values]
    refs = [value.ref for value in values]
    if len(identities) != len(set(identities)) or len(refs) != len(set(refs)):
        raise SoulbondError('Current pairing objects must have unique identities and references')
    return {value.identity.object_id: value for value in values}


def current_pairs(pairs: Iterable[SoulbondPair], objects: Iterable[SoulbondObjectSnapshot]) -> tuple[SoulbondPair, ...]:
    rows = _object_map(objects)
    return tuple(pair for pair in canonical_pair_book(pairs)
                 if all(member.identity.object_id in rows
                        and member.matches(rows[member.identity.object_id], pair.controller)
                        for member in (pair.first, pair.second)))


def pairing_candidates(*, source_ref: str, controller: str,
                       objects: Iterable[SoulbondObjectSnapshot], pairs: Iterable[SoulbondPair] = (),
                       entered_ref: str | None = None) -> tuple[str, ...]:
    _string(source_ref, 'Soulbond source')
    _string(controller, 'Trigger controller')
    if entered_ref is not None:
        _string(entered_ref, 'Entered creature')
    rows = _object_map(objects)
    source = next((row for row in rows.values() if row.ref == source_ref), None)
    occupied = {member.identity.object_id for pair in current_pairs(pairs, rows.values())
                for member in (pair.first, pair.second)}
    if source is None or not source.eligible_for(controller) or source.identity.object_id in occupied:
        return ()
    return tuple(sorted(row.ref for row in rows.values()
                        if row.identity.object_id != source.identity.object_id
                        and row.identity.object_id not in occupied and row.eligible_for(controller)
                        and (entered_ref is None or row.ref == entered_ref)))


def propose_pair(*, pair_id: str, source_ref: str, partner_ref: str, controller: str,
                 objects: Iterable[SoulbondObjectSnapshot], pairs: Iterable[SoulbondPair] = (),
                 entered_ref: str | None = None) -> SoulbondPair:
    rows = _object_map(objects)
    book = canonical_pair_book(pairs)
    if any(pair.pair_id == pair_id for pair in book):
        raise SoulbondError('A committed pair identity cannot be reused')
    if partner_ref not in pairing_candidates(source_ref=source_ref, controller=controller,
            objects=rows.values(), pairs=book, entered_ref=entered_ref):
        raise SoulbondError('Both selected incarnations must currently be eligible and unpaired')
    source = next(row for row in rows.values() if row.ref == source_ref)
    partner = next(row for row in rows.values() if row.ref == partner_ref)
    if source.history is None or partner.history is None:
        raise SoulbondError('Pairing continuity must be captured by the mutation owner')
    return SoulbondPair(pair_id, controller,
        SoulbondMember(source.identity, source.history), SoulbondMember(partner.identity, partner.history))


@dataclass(frozen=True, slots=True)
class SoulbondOpportunity:
    controller: str
    source_ref: str
    source_identity: str
    mode: str
    entered_ref: str | None = None
    entered_identity: str | None = None

    def __post_init__(self) -> None:
        for field in ('controller', 'source_ref', 'source_identity'):
            _string(getattr(self, field), field)
        if type(self.mode) is not str or self.mode not in {'source_entry', 'other_entry'}:
            raise SoulbondError('Soulbond entry opportunity kind is closed')
        if self.mode == 'source_entry':
            if self.entered_ref is not None or self.entered_identity is not None:
                raise SoulbondError('Own entry selects another eligible creature')
        else:
            _string(self.entered_ref, 'Entered reference')
            _string(self.entered_identity, 'Entered incarnation')

    def to_effect(self) -> dict[str, Any]:
        return {'op':SOULBOND_CHOICE_OPERATION, 'player':self.controller,
                'source_ref':self.source_ref, 'source_identity':self.source_identity,
                'mode':self.mode, 'entered_ref':self.entered_ref, 'entered_identity':self.entered_identity}

    @classmethod
    def from_effect(cls, value: Mapping[str, Any]) -> SoulbondOpportunity:
        _fields(value, {'op','player','source_ref','source_identity','mode','entered_ref','entered_identity'}, 'Soulbond opportunity')
        if value['op'] != SOULBOND_CHOICE_OPERATION:
            raise SoulbondError('Soulbond opportunity operation is closed')
        return cls(value['player'], value['source_ref'], value['source_identity'], value['mode'],
                   value['entered_ref'], value['entered_identity'])

    def candidates(self, objects: Iterable[SoulbondObjectSnapshot], pairs: Iterable[SoulbondPair] = ()) -> tuple[str, ...]:
        rows = _object_map(objects)
        source = next((row for row in rows.values() if row.ref == self.source_ref), None)
        if source is None or source.identity.logical_object_id != self.source_identity:
            return ()
        if self.mode == 'other_entry':
            entered = next((row for row in rows.values() if row.ref == self.entered_ref), None)
            if entered is None or entered.identity.logical_object_id != self.entered_identity:
                return ()
        return pairing_candidates(source_ref=self.source_ref, controller=self.controller,
            objects=rows.values(), pairs=pairs, entered_ref=self.entered_ref)
