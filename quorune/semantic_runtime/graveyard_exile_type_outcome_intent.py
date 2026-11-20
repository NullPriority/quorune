"""Immutable direct graveyard target and its creature and land results."""

from dataclasses import dataclass

from ..rules.graveyard_exile_type_outcomes import GraveyardExileTypeOutcomeSpec
from .zone_move_intent import ExilePublicGraveyardCardIntent


@dataclass(frozen=True, slots=True)
class GraveyardExileTypeOutcomeIntent(ExilePublicGraveyardCardIntent):
    result: GraveyardExileTypeOutcomeSpec | None = None

    def __post_init__(self) -> None:
        ExilePublicGraveyardCardIntent.__post_init__(self)
        if any(type(value) is not str for value in (self.actor, self.object_ref, self.reason)):
            raise ValueError('Graveyard exile type outcome identities and reason must be strings')
        if not isinstance(self.result, GraveyardExileTypeOutcomeSpec):
            raise ValueError('Graveyard exile type outcome requires its closed result recipe')
