from __future__ import annotations

"""Immutable request to a registered domain effect owner."""

from dataclasses import dataclass
from ..replacement.immutable import FrozenMap


@dataclass(frozen=True, slots=True)
class DomainEffectIntent:
    actor: str
    operation: str
    effect: FrozenMap
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.effect, FrozenMap):
            object.__setattr__(self, "effect", FrozenMap(self.effect))
