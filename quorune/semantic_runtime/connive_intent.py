from __future__ import annotations

"""Completion publication after the canonical Connive transaction finishes."""

from dataclasses import dataclass

from ..connive import ConnivingObject, FixedConniveSpec


@dataclass(frozen=True, slots=True)
class ConniveCompletedIntent:
    actor: str
    conniver: ConnivingObject
    count: int
    nonlands_discarded: int
    reason: str

    def __post_init__(self):
        if not isinstance(self.conniver, ConnivingObject):
            raise ValueError('Connive completion requires a retained object')
        if self.actor != self.conniver.controller or type(self.reason) is not str or not self.reason:
            raise ValueError('Connive completion requires its controller and reason')
        FixedConniveSpec(self.count)
        if self.count == 0 or type(self.nonlands_discarded) is not int or not 0 <= self.nonlands_discarded <= self.count:
            raise ValueError('Connive completion has invalid committed discard counts')
