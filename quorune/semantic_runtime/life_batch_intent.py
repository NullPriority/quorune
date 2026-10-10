from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from ..replacement.immutable import FrozenMap
from .intents import LifeChangeIntent


@dataclass(frozen=True, slots=True)
class LifeLossBatchIntent:
    """One simultaneous fixed loss instruction over distinct active players."""

    actor: str
    players: tuple[str, ...]
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.players,tuple) or not self.players or len(set(self.players))!=len(self.players) or any(type(p) is not str or not p for p in self.players):
            raise ValueError('Life loss batch requires distinct player identities')
        if type(self.amount) is not int or self.amount<=0:
            raise ValueError('Life loss batch requires a positive fixed amount')
        # Reuse the canonical single-life intent's actor/source/replacement codec.
        validated=LifeChangeIntent(self.actor,self.players[0],-self.amount,self.reason,self.source_ref,self.replacement_selections)
        object.__setattr__(self,'replacement_selections',validated.replacement_selections)

    def identity(self) -> dict[str,Any]:
        return {'actor':self.actor,'players':list(self.players),'amount':self.amount,'reason':self.reason,'source_ref':self.source_ref}
