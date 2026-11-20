"""Closed exclusive outcomes for an announced graveyard card target."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


GRAVEYARD_EXILE_BRANCH_OPERATION = 'exile_public_graveyard_card_branch'
GRAVEYARD_EXILE_BRANCH_CAPABILITY = 'card.exile.public_graveyard_exclusive_outcome'
GRAVEYARD_EXILE_BRANCH_BASE_DEPENDENCIES = (
    GRAVEYARD_EXILE_BRANCH_CAPABILITY,
    'card.exile.public_graveyard',
    'target.revalidate_resolution',
)


class GraveyardExileBranchRecipe(StrEnum):
    CREATURE_LIFE_OR_DRAW = 'creature_life_or_draw'
    OPTIONAL_CREATURE_LIFE_OR_SOURCE_BOOST = 'optional_creature_life_or_source_boost'


class GraveyardExileConsequence(StrEnum):
    GAIN_THREE_LIFE = 'gain_three_life'
    DRAW_ONE = 'draw_one'
    GAIN_TWO_LIFE = 'gain_two_life'
    SOURCE_PLUS_ONE_UNTIL_END_OF_TURN = 'source_plus_one_until_end_of_turn'


@dataclass(frozen=True, slots=True)
class GraveyardExileBranchSpec:
    recipe: GraveyardExileBranchRecipe
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Exclusive graveyard exile requires schema version one')
        if not isinstance(self.recipe, GraveyardExileBranchRecipe):
            raise ValueError('Exclusive graveyard exile requires a typed outcome recipe')

    @property
    def optional(self) -> bool:
        return self.recipe is GraveyardExileBranchRecipe.OPTIONAL_CREATURE_LIFE_OR_SOURCE_BOOST

    def to_dict(self) -> dict[str, str | int]:
        return {'schema_version': self.schema_version, 'recipe': self.recipe.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> 'GraveyardExileBranchSpec':
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'recipe'}:
            raise ValueError('Exclusive graveyard exile descriptor fields are closed')
        name = value['recipe']
        version = value['schema_version']
        if type(name) is not str or type(version) is not int:
            raise ValueError('Exclusive graveyard exile requires a recipe string and integer version')
        return cls(GraveyardExileBranchRecipe(name), version)

    def consequence(self, *, exile_committed: bool, creature_card: bool | None) -> GraveyardExileConsequence | None:
        """Select one outcome using already established public or last-known facts."""
        if type(exile_committed) is not bool:
            raise ValueError('Exile commitment must be explicit')
        if self.optional and not exile_committed:
            return None
        if type(creature_card) is not bool:
            raise ValueError('Exclusive exile outcome needs a known creature-card classification')
        if self.optional:
            return (GraveyardExileConsequence.GAIN_TWO_LIFE if creature_card
                else GraveyardExileConsequence.SOURCE_PLUS_ONE_UNTIL_END_OF_TURN)
        return GraveyardExileConsequence.GAIN_THREE_LIFE if creature_card else GraveyardExileConsequence.DRAW_ONE

    def effect(self) -> dict:
        return {'op': GRAVEYARD_EXILE_BRANCH_OPERATION, 'card': '$target.0', 'result': self.to_dict()}
