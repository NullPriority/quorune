"""One all-or-none repeated opponent life-gain cost, preserving occurrences."""
from dataclasses import dataclass
from .intents import LifeChangeIntent
from ..replacement.immutable import FrozenMap

@dataclass(frozen=True,slots=True)
class LifeGainCostBatchIntent:
    actor: str
    players: tuple[str,...]
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap,...] = ()

    def __post_init__(self):
        if not isinstance(self.players,tuple) or not self.players or any(type(p)is not str or not p or p==self.actor for p in self.players):
            raise ValueError('Opponent gain cost requires a nonempty ordered opponent list')
        if type(self.amount)is not int or self.amount<1:raise ValueError('Gain cost requires one positive fixed unit amount')
        validated=LifeChangeIntent(self.actor,self.players[0],self.amount,self.reason,self.source_ref,self.replacement_selections)
        object.__setattr__(self,'replacement_selections',validated.replacement_selections)

    def identity(self):
        return {'actor':self.actor,'players':list(self.players),'amount':self.amount,'reason':self.reason,'source_ref':self.source_ref}
