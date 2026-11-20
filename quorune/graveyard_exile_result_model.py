"""Closed type/life consequence for one specifically chosen graveyard card."""
from collections.abc import Mapping
from dataclasses import dataclass

from .target_forms import TargetCharacteristicForm


GRAVEYARD_EXILE_LIFE_RESULT_CAPABILITY = 'choice.affected_player.graveyard_exile_creature_life'
GRAVEYARD_EXILE_LIFE_RESULT_MECHANIC = 'selected-graveyard-card-type-life'

_CREATURE_CARD_FORM = TargetCharacteristicForm(types_all=('creature',))


@dataclass(frozen=True, slots=True)
class GraveyardExileLifeResultSpec:
    amount: int
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Selected graveyard-card life result requires version one')
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError('Selected graveyard-card life gain must be a positive fixed integer')

    @property
    def card_form(self) -> TargetCharacteristicForm:
        return _CREATURE_CARD_FORM

    def to_dict(self):
        return {'schema_version': self.schema_version, 'card_type': 'creature', 'life_gain': self.amount}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'card_type', 'life_gain'}
                or value.get('card_type') != 'creature'):
            raise ValueError('Selected graveyard-card result fields and creature-card condition are closed')
        return cls(amount=value['life_gain'], schema_version=value['schema_version'])
