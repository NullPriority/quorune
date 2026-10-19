"""Identity-pinned pairing commit produced by the optional Soulbond choice."""
from __future__ import annotations

from dataclasses import dataclass
from ..soulbond_model import SoulbondOpportunity


@dataclass(frozen=True, slots=True)
class PairSoulbondIntent:
    actor: str
    source_ref: str
    source_identity: str
    partner_ref: str
    partner_identity: str
    pair_id: str
    reason: str
    entered_ref: str | None = None
    entered_identity: str | None = None

    def __post_init__(self) -> None:
        for value in (self.actor, self.source_ref, self.source_identity, self.partner_ref,
                      self.partner_identity, self.pair_id, self.reason):
            if type(value) is not str or not value:
                raise ValueError('Soulbond intent requires pinned exact identities')
        SoulbondOpportunity(self.actor, self.source_ref, self.source_identity,
            'source_entry' if self.entered_ref is None else 'other_entry',
            self.entered_ref, self.entered_identity)
