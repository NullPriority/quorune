"""Keep the immutable token recipe with the announced graveyard target."""

from dataclasses import dataclass

from ..graveyard_exile_token_model import GraveyardExileTokenSpec
from .zone_move_intent import ExilePublicGraveyardCardIntent


@dataclass(frozen=True, slots=True)
class GraveyardExileTokenIntent(ExilePublicGraveyardCardIntent):
    result: GraveyardExileTokenSpec | None = None

    def __post_init__(self) -> None:
        ExilePublicGraveyardCardIntent.__post_init__(self)
        if any(type(value) is not str for value in (self.actor, self.object_ref, self.reason)):
            raise ValueError('Graveyard exile token identities and reason must be strings')
        if not isinstance(self.result, GraveyardExileTokenSpec):
            raise ValueError('Graveyard exile token requires a fixed result recipe')
