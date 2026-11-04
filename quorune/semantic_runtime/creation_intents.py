from __future__ import annotations

from dataclasses import dataclass

from ..emblem_program_model import CompiledEmblemSpec
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class CreateCompiledEmblemIntent:
    player: str
    emblem: CompiledEmblemSpec
    reason: str

    def __post_init__(self):
        if (type(self.player) is not str or not self.player or type(self.reason) is not str
                or not self.reason or not isinstance(self.emblem, CompiledEmblemSpec)):
            raise SemanticNodeError('Compiled emblem creation requires its player, complete specification and provenance')


@dataclass(frozen=True, slots=True)
class CreateRegenerationShieldIntent:
    actor: str
    object_ref: str
    logical_object_id: str | None
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.object_ref,
                self.reason,
            )
        ) or (
            self.logical_object_id is not None
            and (
                type(self.logical_object_id) is not str
                or not self.logical_object_id
            )
        ):
            raise ValueError(
                "Regeneration intents require actor, object, optional incarnation, and reason"
            )
