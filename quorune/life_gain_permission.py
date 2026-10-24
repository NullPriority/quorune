from __future__ import annotations

"""Immutable CR 119.7 permission values, separate from replacements."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


LIFE_GAIN_PROHIBITION_CAPABILITY = 'life.gain.prohibition.static'
LIFE_GAIN_PROHIBITION_HANDLER = 'life.gain.prohibition.v1'
LIFE_GAIN_PROHIBITION_EVENT = 'life.gain.permission'


@dataclass(frozen=True, slots=True)
class LifeGainPermissionSnapshot:
    denied_players: tuple[str, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.denied_players, tuple)
                or any(type(p) is not str or not p for p in self.denied_players)
                or self.denied_players != tuple(sorted(set(self.denied_players)))):
            raise ValueError('Life-gain permissions require unique canonical player IDs')

    def allows_gain(self, player: str) -> bool:
        if type(player) is not str or not player:
            raise ValueError('Life-gain permissions require a player ID')
        return player not in self.denied_players


ALLOW_ALL_LIFE_GAIN = LifeGainPermissionSnapshot()


@dataclass(frozen=True, slots=True)
class FixedLifeGainProhibitionSpec:
    affected_player_relation: str

    def __post_init__(self):
        if type(self.affected_player_relation) is not str or self.affected_player_relation not in {'any', 'opponent'}:
            raise ValueError('Unsupported static life-gain prohibition relation')

    def to_dict(self):
        return {'schema_version': 1, 'affected_player_relation': self.affected_player_relation}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'affected_player_relation'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ValueError('Malformed static life-gain prohibition')
        return cls(value['affected_player_relation'])

    def permissions(self, controller: str, active_players: Sequence[str]) -> LifeGainPermissionSnapshot:
        if (isinstance(active_players, (str, bytes)) or not active_players
                or any(type(p) is not str or not p for p in active_players)
                or len(set(active_players)) != len(active_players) or controller not in active_players):
            raise ValueError('Life-gain prohibitions require a current active controller')
        return LifeGainPermissionSnapshot(tuple(sorted(p for p in active_players
            if self.affected_player_relation == 'any' or p != controller)))


def decode_life_gain_prohibition(descriptor):
    if (not isinstance(descriptor, Mapping)
            or set(descriptor) != {'handler_id','schema_version','event','condition'}
            or descriptor['handler_id'] != LIFE_GAIN_PROHIBITION_HANDLER
            or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
            or descriptor['event'] != LIFE_GAIN_PROHIBITION_EVENT):
        raise ValueError('Malformed life-gain prohibition descriptor')
    return FixedLifeGainProhibitionSpec.from_dict(descriptor['condition'])


def life_gain_prohibition_payload_dependencies(value, *, coverage=()):
    if not isinstance(value, Mapping) or value.get('handler_id') != LIFE_GAIN_PROHIBITION_HANDLER:
        return ()
    decode_life_gain_prohibition(value)
    from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    if CURRENT_ABILITY_FRAGMENT_COVERAGE not in coverage:
        raise ValueError('Life-gain prohibition requires current source-ability scope')
    return (LIFE_GAIN_PROHIBITION_CAPABILITY,)
