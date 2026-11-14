"""Closed public metrics and a unique-current-maximum player reference."""
from collections.abc import Mapping
from dataclasses import dataclass
from .creature_subtypes import CREATURE_SUBTYPES
from .object_predicate import ObjectQuerySpec

PLAYER_MAXIMUM_CAPABILITY='condition.player.unique_maximum'
PLAYER_MAXIMUM_MECHANIC='public-player-unique-maximum'
PLAYER_MAXIMUM_FACT='unique_player_maximum'
PLAYER_MAXIMUM_REFERENCE='unique_player_maximum'


@dataclass(frozen=True,slots=True)
class PlayerMaximumSpec:
    metric: str

    def __post_init__(self):
        if type(self.metric) is not str:raise ValueError('Player maximum requires a closed public metric')
        if self.metric in {'life','hand_count','creature_count'}:return
        if not self.metric.startswith('subtype:') or self.metric[8:] not in CREATURE_SUBTYPES:
            raise ValueError('Player maximum metric is unsupported')

    def predicate(self):
        if self.metric=='creature_count':return ObjectQuerySpec(zones=('battlefield',),types_all=('creature',))
        if self.metric.startswith('subtype:'):return ObjectQuerySpec(zones=('battlefield',),subtypes_all=(self.metric[8:],))
        return None

    def to_reference(self):
        return {'kind':PLAYER_MAXIMUM_REFERENCE,'schema_version':1,'metric':self.metric}

    @classmethod
    def from_reference(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','metric'}
                or value['kind']!=PLAYER_MAXIMUM_REFERENCE or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise ValueError('Unique player reference requires its closed metric and version')
        return cls(value['metric'])


def validate_player_maximum_fact(*,schema_version,amount,parameter):
    if type(schema_version) is not int or schema_version!=4 or type(amount) is not int or amount!=1:
        raise ValueError('Unique player maximum requires one versioned boolean fact')
    return PlayerMaximumSpec(parameter)
