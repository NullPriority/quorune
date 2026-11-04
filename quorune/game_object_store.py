from __future__ import annotations

"""Canonical registration and detachment for the existing current-object map."""

from collections.abc import Sequence
from typing import Protocol
from .model import CardInstance,GameState


class GameObjectStoreHost(Protocol):
    state: GameState


class GameObjectStore:
    def __init__(self,host:GameObjectStoreHost):
        self.host=host

    @property
    def state(self)->GameState:
        return self.host.state

    def commit(self,*,upserts:Sequence[CardInstance]=(),removals:Sequence[str]=())->tuple[CardInstance,...]:
        """Validate the complete map edit before changing any membership."""
        if (not isinstance(upserts,(list,tuple)) or not isinstance(removals,(list,tuple))
                or any(not isinstance(card,CardInstance) or type(card.object_id) is not str
                    or not card.object_id for card in upserts)
                or any(type(object_id) is not str or not object_id for object_id in removals)):
            raise ValueError('Current-object edits require typed objects and explicit physical identities')
        additions={card.object_id:card for card in upserts}
        if (len(additions)!=len(upserts) or len(removals)!=len(set(removals))
                or any(object_id not in self.state.cards for object_id in removals)):
            raise ValueError('Current-object edits contain duplicate or missing identities')
        removed=tuple(self.state.cards[object_id] for object_id in removals)
        for object_id in removals:self.state.cards.pop(object_id)
        self.state.cards.update(additions)
        return removed
