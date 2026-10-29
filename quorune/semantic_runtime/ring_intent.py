"""Typed requests for the owned Ring progress and creature-choice boundary."""
from __future__ import annotations

from dataclasses import dataclass

from ..ring_model import RingBearerChoicePlan, RingStateError


@dataclass(frozen=True, slots=True)
class BeginRingTemptationIntent:
    player: str
    reason: str

    def __post_init__(self):
        if any(type(value) is not str or not value.strip() for value in (self.player, self.reason)):
            raise RingStateError('Ring progress requires its player and provenance')


@dataclass(frozen=True, slots=True)
class CompleteRingBearerIntent:
    plan: RingBearerChoicePlan
    selected_ref: str | None
    reason: str

    def __post_init__(self):
        if not isinstance(self.plan, RingBearerChoicePlan):
            raise RingStateError('Ring completion requires a typed retained plan')
        if self.selected_ref is not None and (type(self.selected_ref) is not str or not self.selected_ref):
            raise RingStateError('Ring completion requires an exact selected reference')
        if type(self.reason) is not str or not self.reason.strip():
            raise RingStateError('Ring completion requires effect provenance')

    @property
    def player(self):
        return self.plan.state.player
