from __future__ import annotations

"""Typed choice within one resolving Miracle trigger, including a copy."""

from dataclasses import dataclass, field
from typing import Literal

from ..drawing.miracle_model import MiracleRevealedHand
from ..replacement.immutable import FrozenMap
from .context import SemanticSourceContext


@dataclass(frozen=True, slots=True)
class MiracleCastChoiceIntent:
    actor: str
    source: SemanticSourceContext
    hand: MiracleRevealedHand
    choice: Literal['cast', 'decline']
    options_fingerprint: str
    response: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self):
        if (type(self.actor) is not str or not self.actor
                or not isinstance(self.source, SemanticSourceContext)
                or not isinstance(self.hand, MiracleRevealedHand)
                or self.choice not in {'cast', 'decline'}
                or type(self.options_fingerprint) is not str or not self.options_fingerprint
                or not isinstance(self.response, FrozenMap)):
            raise ValueError('Miracle choice requires a trigger, actor, hand incarnation and current options')
        if (self.source.object_id, self.source.logical_object_id, self.source.card_ref) != (
            self.hand.card_object_id, self.hand.hand_logical_object_id, self.hand.hand_ref
        ):
            raise ValueError('Miracle choice source differs from its linked hand incarnation')
