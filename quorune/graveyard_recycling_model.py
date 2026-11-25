"""Closed whole-graveyard recycling instruction, independent of library order."""
from collections.abc import Mapping
from dataclasses import dataclass

GRAVEYARD_RECYCLING_OPERATION = 'recycle_graveyards_into_libraries'
GRAVEYARD_RECYCLING_CAPABILITY = 'zone.shuffle.whole_graveyard'
HAND_GRAVEYARD_RECYCLING_CAPABILITY = 'zone.shuffle.hand_and_graveyard'
SOURCE_GRAVEYARD_RECYCLING_VERSION = 3
GRAVEYARD_RECYCLING_SCOPES = frozenset({'controller', 'target_player', 'all_players'})


def uses_source_graveyard_recycling(effect):
    recycling = effect.get("recycling")
    return (effect.get("op") == GRAVEYARD_RECYCLING_OPERATION and isinstance(recycling, Mapping)
            and recycling.get("schema_version") == SOURCE_GRAVEYARD_RECYCLING_VERSION)


@dataclass(frozen=True, slots=True)
class GraveyardRecyclingSpec:
    scope: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.scope) is not str or self.scope not in GRAVEYARD_RECYCLING_SCOPES
                or type(self.schema_version) is not int or self.schema_version not in {1, 2, SOURCE_GRAVEYARD_RECYCLING_VERSION}
                or self.include_hand and self.scope not in {'controller','all_players'}
                or self.include_source and self.scope != 'controller'):
            raise ValueError('Whole-graveyard recycling needs one exact scope and version')

    @property
    def include_hand(self):
        return self.schema_version == 2

    @property
    def include_source(self):
        return self.schema_version == SOURCE_GRAVEYARD_RECYCLING_VERSION

    @property
    def origins(self):
        return ('hand','graveyard') if self.include_hand else ('graveyard',)

    def to_dict(self):
        value = {'scope': self.scope, 'schema_version': self.schema_version}
        if self.include_hand:
            value['include_hand'] = True
        if self.include_source:
            value['include_source'] = True
        return value

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError('Whole-graveyard recycling fields are incomplete or unknown')
        version = value.get('schema_version')
        additional = 'include_hand' if version == 2 else (
            'include_source' if version == SOURCE_GRAVEYARD_RECYCLING_VERSION else None
        )
        expected = {'scope', 'schema_version'} | ({additional} if additional else set())
        if set(value) != expected or additional and value[additional] is not True:
            raise ValueError('Whole-graveyard recycling fields are incomplete or unknown')
        return cls(value['scope'], version)
