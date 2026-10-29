"""Immutable CR 701.54 Ring progress and non-target bearer choice values."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .source_continuity import SourceContinuitySnapshot

RING_TEMPTATION_OPERATION = 'tempt_with_the_ring'
RING_TEMPTATION_CAPABILITY = 'designation.ring.temptation'
RING_TEMPTED_EVENT = 'player.ring_tempted'
RING_MECHANIC = 'the-ring-tempts-you'
RING_BEARER_CAPABILITY = 'designation.ring.bearer'
RING_STATIC_CAPABILITY = 'continuous.ring.bearer_rules'
RING_ATTACK_CAPABILITY = 'trigger.ring.attack_loot'
RING_BLOCK_CAPABILITY = 'trigger.ring.blocker_sacrifice'
RING_DAMAGE_CAPABILITY = 'trigger.ring.combat_player_loss'


class RingStateError(ValueError):
    """Ring state or a sealed bearer selection is malformed."""


def _string(value: Any, field: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise RingStateError(f'{field} must be a nonempty exact string')


def _fields(value: Any, expected: set[str], field: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RingStateError(f'{field} has missing or unknown fields')


def _version(value: Any) -> None:
    if type(value) is not int or value != 1:
        raise RingStateError('Unsupported Ring state version')


@dataclass(frozen=True, slots=True)
class RingObjectIdentity:
    object_id: str
    object_ref: str
    logical_object_id: str

    def __post_init__(self) -> None:
        for field in ('object_id', 'object_ref', 'logical_object_id'):
            _string(getattr(self, field), field)

    def to_dict(self) -> dict[str, str]:
        return {'object_id': self.object_id, 'object_ref': self.object_ref,
                'logical_object_id': self.logical_object_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RingObjectIdentity:
        _fields(value, {'object_id', 'object_ref', 'logical_object_id'}, 'Ring object identity')
        return cls(value['object_id'], value['object_ref'], value['logical_object_id'])


@dataclass(frozen=True, slots=True)
class RingBearerSnapshot:
    identity: RingObjectIdentity
    history: SourceContinuitySnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RingObjectIdentity) or not isinstance(self.history, SourceContinuitySnapshot):
            raise RingStateError('A bearer requires its incarnation and known control history')

    def control_is_continuous(self, current: SourceContinuitySnapshot) -> bool:
        if not isinstance(current, SourceContinuitySnapshot):
            raise RingStateError('Current bearer control history must be known')
        return self.history.control_epoch == current.control_epoch

    def to_dict(self) -> dict[str, Any]:
        return {'identity': self.identity.to_dict(), 'history': self.history.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RingBearerSnapshot:
        _fields(value, {'identity', 'history'}, 'Ring bearer snapshot')
        try:
            history = SourceContinuitySnapshot.from_dict(value['history'])
        except (TypeError, ValueError) as exc:
            raise RingStateError('A bearer requires exact control history') from exc
        return cls(RingObjectIdentity.from_dict(value['identity']), history)


@dataclass(frozen=True, slots=True)
class RingEmblemState:
    player: str
    emblem: RingObjectIdentity
    temptation_count: int
    bearer: RingBearerSnapshot | None = None

    def __post_init__(self) -> None:
        _string(self.player, 'Ring player')
        if not isinstance(self.emblem, RingObjectIdentity):
            raise RingStateError('The Ring requires a typed emblem incarnation')
        if type(self.temptation_count) is not int or self.temptation_count < 1:
            raise RingStateError('Ring temptation count must be a positive exact integer')
        if self.bearer is not None:
            if not isinstance(self.bearer, RingBearerSnapshot):
                raise RingStateError('The bearer requires a typed incarnation')
            if any(getattr(self.emblem, field) == getattr(self.bearer.identity, field)
                   for field in ('object_id', 'object_ref', 'logical_object_id')):
                raise RingStateError('The emblem and bearer must be distinct objects')

    @property
    def ability_level(self) -> int:
        return min(self.temptation_count, 4)

    def tempted_again(self) -> RingEmblemState:
        return replace(self, temptation_count=self.temptation_count + 1)

    def without_bearer(self) -> RingEmblemState:
        return replace(self, bearer=None)

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': 1, 'player': self.player,
                'emblem': self.emblem.to_dict(), 'temptation_count': self.temptation_count,
                'bearer': self.bearer.to_dict() if self.bearer is not None else None}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RingEmblemState:
        _fields(value, {'schema_version', 'player', 'emblem', 'temptation_count', 'bearer'}, 'Ring state')
        _version(value['schema_version'])
        return cls(value['player'], RingObjectIdentity.from_dict(value['emblem']),
                   value['temptation_count'],
                   RingBearerSnapshot.from_dict(value['bearer']) if value['bearer'] is not None else None)


@dataclass(frozen=True, slots=True)
class RingBearerChoice:
    state: RingEmblemState
    chosen_bearer: RingBearerSnapshot | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, RingEmblemState):
            raise RingStateError('A bearer choice result requires typed Ring state')
        if self.chosen_bearer is not None and (
            not isinstance(self.chosen_bearer, RingBearerSnapshot)
            or self.chosen_bearer != self.state.bearer
        ):
            raise RingStateError('The chosen creature must match the resulting bearer')

    @property
    def creature_chosen(self) -> bool:
        return self.chosen_bearer is not None


@dataclass(frozen=True, slots=True)
class RingBearerChoicePlan:
    """Sealed choices whose commit owner rechecks live control and eligibility."""

    state: RingEmblemState
    candidates: tuple[RingBearerSnapshot, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.state, RingEmblemState):
            raise RingStateError('A bearer choice requires typed Ring state')
        if type(self.candidates) is not tuple or any(
            not isinstance(candidate, RingBearerSnapshot) for candidate in self.candidates
        ):
            raise RingStateError('Bearer choices require immutable typed incarnations')
        for field in ('object_id', 'object_ref', 'logical_object_id'):
            values = [getattr(candidate.identity, field) for candidate in self.candidates]
            if len(values) != len(set(values)):
                raise RingStateError('Bearer choices must contain distinct objects')
            if getattr(self.state.emblem, field) in values:
                raise RingStateError('The Ring emblem cannot be a bearer choice')

    @property
    def needs_choice(self) -> bool:
        return bool(self.candidates)

    def select(self, selected: RingBearerSnapshot | None) -> RingBearerChoice:
        if selected is None:
            if self.candidates:
                raise RingStateError('A creature must be chosen when one is available')
            # An impossible choice does not replace an existing designation.
            return RingBearerChoice(self.state, None)
        if not isinstance(selected, RingBearerSnapshot) or selected not in self.candidates:
            raise RingStateError('The selected bearer is not a sealed choice incarnation')
        return RingBearerChoice(replace(self.state, bearer=selected), selected)

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': 1, 'state': self.state.to_dict(),
                'candidates': [candidate.to_dict() for candidate in self.candidates]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RingBearerChoicePlan:
        _fields(value, {'schema_version', 'state', 'candidates'}, 'Ring bearer choice')
        _version(value['schema_version'])
        if type(value['candidates']) is not list:
            raise RingStateError('Serialized bearer choices require a list')
        return cls(RingEmblemState.from_dict(value['state']),
                   tuple(RingBearerSnapshot.from_dict(candidate) for candidate in value['candidates']))


@dataclass(frozen=True, slots=True)
class RingRuleView:
    """Current immutable source facts for an emblem's inherent rules effects."""
    state: RingEmblemState
    timestamp: int

    def __post_init__(self):
        if not isinstance(self.state, RingEmblemState):
            raise RingStateError('Ring rule effects require typed current emblem state')
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise RingStateError('Ring rule effects require their exact source timestamp')
