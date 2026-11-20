"""Keep the typed recipe and original stack source together through exile."""

from dataclasses import dataclass

from ..graveyard_exile_branches_model import GraveyardExileBranchSpec
from .zone_move_intent import ExilePublicGraveyardCardIntent


@dataclass(frozen=True, slots=True)
class GraveyardExileBranchIntent(ExilePublicGraveyardCardIntent):
    result: GraveyardExileBranchSpec | None = None
    source_object_id: str | None = None
    source_logical_object_id: str | None = None

    def __post_init__(self) -> None:
        ExilePublicGraveyardCardIntent.__post_init__(self)
        if any(type(value) is not str for value in (self.actor, self.object_ref, self.reason)):
            raise ValueError('Exclusive graveyard exile identities and reason must be strings')
        if not isinstance(self.result, GraveyardExileBranchSpec):
            raise ValueError('Exclusive graveyard exile needs its typed recipe')
        if (self.source_object_id is None) != (self.source_logical_object_id is None):
            raise ValueError('Exclusive exile source identities must both be supplied or absent')
        for value in (self.source_object_id, self.source_logical_object_id):
            if value is not None and (type(value) is not str or not value):
                raise ValueError('Exclusive exile source identities must be nonempty strings')
