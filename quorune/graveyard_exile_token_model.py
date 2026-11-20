"""Typed fixed token creation after an announced graveyard exile."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .replacement.immutable import thaw_value
from .rules.committed_quantity_followups import fixed_quantity_effect


GRAVEYARD_EXILE_TOKEN_OPERATION = 'exile_public_graveyard_card_for_token'
GRAVEYARD_EXILE_TOKEN_CAPABILITY = 'card.exile.public_graveyard_fixed_creature_token'
GRAVEYARD_EXILE_TOKEN_BASE_DEPENDENCIES = (
    GRAVEYARD_EXILE_TOKEN_CAPABILITY,
    'card.exile.public_graveyard',
    'target.revalidate_resolution',
)


class ExiledCreatureCardPredicate(StrEnum):
    CREATURE_CARD = 'creature_card'
    COMMITTED_CREATURE_CARD = 'committed_creature_card'


@dataclass(frozen=True, slots=True)
class GraveyardExileTokenSpec:
    predicate: ExiledCreatureCardPredicate
    token_effect: Mapping[str, object]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Graveyard exile token recipe requires schema version one')
        if not isinstance(self.predicate, ExiledCreatureCardPredicate):
            raise ValueError('Graveyard exile token recipe requires a creature-card predicate')
        if (not isinstance(self.token_effect, Mapping)
                or set(self.token_effect) != {'op', 'controller', 'quantity', 'name', 'characteristics'}
                or self.token_effect['op'] != 'create_token'):
            raise ValueError('Graveyard exile token result must be a single ordinary creature token')
        properties = self.token_effect['characteristics']
        if (not isinstance(properties, Mapping)
                or not str(properties.get('type_line', '')).startswith('Token Creature — ')
                or any(type(properties.get(stat)) is not str or not properties[stat].isdigit()
                    for stat in ('power', 'toughness'))):
            raise ValueError('Graveyard exile token requires a fixed ordinary creature definition')
        object.__setattr__(self, 'token_effect', fixed_quantity_effect(self.token_effect, allow_typed_token_ability=True))

    @property
    def requires_committed_exile(self) -> bool:
        return self.predicate is ExiledCreatureCardPredicate.COMMITTED_CREATURE_CARD

    def should_create(self, *, exile_committed: bool, creature_card: bool | None) -> bool:
        if type(exile_committed) is not bool:
            raise ValueError('Graveyard exile token needs an explicit commit result')
        if self.requires_committed_exile and not exile_committed:
            return False
        if type(creature_card) is not bool:
            raise ValueError('Graveyard exile token needs a known creature-card classification')
        return creature_card

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'predicate': self.predicate.value,
            'token_effect': thaw_value(self.token_effect),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> 'GraveyardExileTokenSpec':
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'predicate', 'token_effect'}:
            raise ValueError('Graveyard exile token recipe fields are closed')
        if type(value['predicate']) is not str:
            raise ValueError('Graveyard exile token predicate must be text')
        return cls(ExiledCreatureCardPredicate(value['predicate']), value['token_effect'], value['schema_version'])

    def effect(self) -> dict:
        return {'op': GRAVEYARD_EXILE_TOKEN_OPERATION, 'card': '$target.0', 'result': self.to_dict()}
