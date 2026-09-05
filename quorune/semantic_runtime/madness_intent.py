from __future__ import annotations

"""Typed mutation intent for a resolved Madness choice."""

from dataclasses import dataclass, field
from typing import Literal

from ..cast_lifecycles import FixedCastLifecycleKind, FixedCastLifecycleSpec
from ..replacement.immutable import FrozenMap
from .context import SemanticSourceContext


@dataclass(frozen=True, slots=True)
class MadnessChoiceIntent:
    actor: str
    source: SemanticSourceContext
    lifecycle: FixedCastLifecycleSpec
    reason: str
    choice: Literal["cast", "decline"]
    response: FrozenMap = field(default_factory=FrozenMap)
    options_fingerprint: str = ""

    def __post_init__(self) -> None:
        if (
            type(self.actor) is not str
            or not self.actor
            or not isinstance(self.source, SemanticSourceContext)
            or self.source.object_id is None
            or self.source.logical_object_id is None
            or self.source.card_ref is None
            or not isinstance(self.lifecycle, FixedCastLifecycleSpec)
            or self.lifecycle.kind is not FixedCastLifecycleKind.MADNESS
            or type(self.reason) is not str
            or not self.reason
            or self.choice not in {"cast", "decline"}
            or not isinstance(self.response, FrozenMap)
            or type(self.options_fingerprint) is not str
            or not self.options_fingerprint
        ):
            raise ValueError(
                "Madness choices require a current actor, source, lifecycle, response, and option identity"
            )


__all__ = ["MadnessChoiceIntent"]
