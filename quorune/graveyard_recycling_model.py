"""Closed whole-graveyard recycling instruction, independent of library order."""
from collections.abc import Mapping
from dataclasses import dataclass

GRAVEYARD_RECYCLING_OPERATION = 'recycle_graveyards_into_libraries'
GRAVEYARD_RECYCLING_CAPABILITY = 'zone.shuffle.whole_graveyard'
HAND_GRAVEYARD_RECYCLING_CAPABILITY = 'zone.shuffle.hand_and_graveyard'
GRAVEYARD_RECYCLING_SCOPES = frozenset({'controller', 'target_player', 'all_players'})


@dataclass(frozen=True, slots=True)
class GraveyardRecyclingSpec:
    scope: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.scope) is not str or self.scope not in GRAVEYARD_RECYCLING_SCOPES
                or type(self.schema_version) is not int or self.schema_version not in {1,2}
                or self.include_hand and self.scope not in {'controller','all_players'}):
            raise ValueError('Whole-graveyard recycling needs one exact scope and version')

    @property
    def include_hand(self):
        return self.schema_version == 2

    @property
    def origins(self):
        return ('hand','graveyard') if self.include_hand else ('graveyard',)

    def to_dict(self):
        value = {'scope': self.scope, 'schema_version': self.schema_version}
        if self.schema_version == 2:value['include_hand'] = self.include_hand
        return value

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != ({'scope','schema_version','include_hand'}
                    if value.get('schema_version') == 2 else {'scope','schema_version'})):
            raise ValueError('Whole-graveyard recycling fields are incomplete or unknown')
        if value['schema_version'] == 2 and value['include_hand'] is not True:
            raise ValueError('Version-two recycling requires the complete hand collection')
        return cls(value['scope'],value['schema_version'])
