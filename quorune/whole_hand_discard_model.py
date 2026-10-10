from __future__ import annotations

"""Closed whole-hand discard request with no chosen subset."""

from dataclasses import dataclass
from typing import Mapping
from .replacement.immutable import FrozenMap

WHOLE_HAND_DISCARD_OPERATION='discard_whole_hands'
WHOLE_HAND_DISCARD_CAPABILITY='zone.discard.whole_hand'
WHOLE_HAND_DISCARD_MECHANIC='whole-hand-discard'


@dataclass(frozen=True,slots=True)
class DiscardWholeHandsIntent:
    actor: str
    players: tuple[str,...]
    reason: str
    replacement_selections: tuple[str|FrozenMap,...]=()

    def __post_init__(self):
        if type(self.actor) is not str or not self.actor or type(self.reason) is not str or not self.reason:
            raise ValueError('Whole-hand discard requires actor and reason')
        if not isinstance(self.players,(list,tuple)) or any(type(p) is not str or not p for p in self.players) or len(self.players)!=len(set(self.players)):
            raise ValueError('Whole-hand discard players are malformed')
        object.__setattr__(self,'players',tuple(self.players))
        if not isinstance(self.replacement_selections,(list,tuple)) or any(not isinstance(v,(str,Mapping)) or (isinstance(v,str) and not v) for v in self.replacement_selections):
            raise ValueError('Whole-hand discard replacements are malformed')
        object.__setattr__(self,'replacement_selections',tuple(FrozenMap(v) if isinstance(v,Mapping) else v for v in self.replacement_selections))
