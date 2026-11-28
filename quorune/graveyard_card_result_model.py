"""Closed card-type results for a source counter after targeted graveyard exile."""
from collections.abc import Mapping
from dataclasses import dataclass

from .target_forms import TargetCharacteristicForm


GRAVEYARD_CARD_COUNTER_LIFE_CAPABILITY = 'card.exile.public_graveyard_creature_counter_life'
GRAVEYARD_CARD_COUNTER_LIFE_MECHANIC = 'targeted-graveyard-card-creature-counter-life'
GRAVEYARD_CARD_COUNTER_LIFE_OPERATION = 'exile_public_graveyard_card_result'
GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES = (GRAVEYARD_CARD_COUNTER_LIFE_CAPABILITY,
    'card.exile.public_graveyard', 'target.revalidate_resolution', 'counter.producer.fixed_effect', 'life.change.effect')
GRAVEYARD_PERMANENT_CARD_COUNTER_CAPABILITY = 'card.exile.public_graveyard_permanent_source_counter'
GRAVEYARD_PERMANENT_CARD_COUNTER_DEPENDENCIES = (
    GRAVEYARD_PERMANENT_CARD_COUNTER_CAPABILITY,
    'card.exile.public_graveyard',
    'target.revalidate_resolution',
    'counter.producer.fixed_effect',
)

_CREATURE_CARD_FORM = TargetCharacteristicForm(types_all=('creature',))


@dataclass(frozen=True, slots=True)
class GraveyardCardCounterLifeSpec:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Graveyard card counter/life result requires explicit version one')

    @property
    def card_form(self) -> TargetCharacteristicForm:
        return _CREATURE_CARD_FORM

    @property
    def dependencies(self) -> tuple[str, ...]:
        return GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES

    def to_dict(self):
        return {'schema_version': self.schema_version, 'card_type': 'creature',
            'source_counter': {'counter': '+1/+1', 'amount': 1}, 'life_gain': 1}

    def effect(self, *, card='$target.0'):
        if type(card) is not str or not card:
            raise ValueError('Graveyard card result requires its one target reference')
        return {'op': GRAVEYARD_CARD_COUNTER_LIFE_OPERATION, 'card': card, 'result': self.to_dict()}

    @classmethod
    def from_effect(cls, value, *, card='$target.0'):
        if (not isinstance(value, Mapping) or set(value) != {'op', 'card', 'result'}
                or value.get('op') != GRAVEYARD_CARD_COUNTER_LIFE_OPERATION
                or type(card) is not str or not card or value.get('card') != card):
            raise ValueError('Graveyard card result instruction and direct target role are closed')
        return cls.from_dict(value['result'])

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {'schema_version', 'card_type', 'source_counter', 'life_gain'}
                or value.get('card_type') != 'creature'):
            raise ValueError('Targeted graveyard card result fields and condition are closed')
        counter = value['source_counter']
        if (not isinstance(counter, Mapping) or set(counter) != {'counter', 'amount'}
                or counter.get('counter') != '+1/+1' or type(counter.get('amount')) is not int or counter['amount'] != 1
                or type(value['life_gain']) is not int or value['life_gain'] != 1):
            raise ValueError('Targeted graveyard card result owns one source counter and one life')
        return cls(value['schema_version'])


@dataclass(frozen=True, slots=True)
class GraveyardPermanentCardCounterSpec(GraveyardCardCounterLifeSpec):
    schema_version: int = 2

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValueError('Graveyard permanent card counter requires explicit version two')

    @property
    def dependencies(self) -> tuple[str, ...]:
        return GRAVEYARD_PERMANENT_CARD_COUNTER_DEPENDENCIES

    def to_dict(self):
        return {
            'schema_version': self.schema_version,
            'card_type': 'permanent',
            'source_counter': {'counter': '+1/+1', 'amount': 1},
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'card_type', 'source_counter'}:
            raise ValueError('Graveyard permanent card counter descriptor fields are closed')
        counter = value['source_counter']
        if (
            value['card_type'] != 'permanent'
            or not isinstance(counter, Mapping)
            or set(counter) != {'counter', 'amount'}
            or counter['counter'] != '+1/+1'
            or type(counter['amount']) is not int
            or counter['amount'] != 1
        ):
            raise ValueError('Graveyard permanent card result requires one +1/+1 source counter')
        return cls(value['schema_version'])


def graveyard_card_counter_recipe(value) -> type[GraveyardCardCounterLifeSpec]:
    if isinstance(value, Mapping) and value.get('schema_version') == 2:
        return GraveyardPermanentCardCounterSpec
    return GraveyardCardCounterLifeSpec
