"""Closed version-one event-player selection for resolution-locked stat effects."""
from collections.abc import Mapping
from dataclasses import dataclass
from .object_predicate import ObjectQuerySpec

EVENT_PLAYER_SET_VERSION_FIELD='event_player_set_version'
EVENT_PLAYER_SET_CAPABILITY='resolution.event_player.fixed_creature_stat_set'
EVENT_PLAYER_SET_MECHANIC='event-player-fixed-creature-stat-set'
EVENT_PLAYER_SET_OPERATION='modify_all_matching_permanents_until_end_of_turn'
DEFENDING_PLAYER_REFERENCE='$context.defending_player'


@dataclass(frozen=True,slots=True)
class EventPlayerModifierSpec:
    power: int
    toughness: int
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.power) is not int or type(self.toughness) is not int
                or not (self.power or self.toughness)):
            raise ValueError('Event-player modifier requires explicit version one and nonzero fixed integer stats')

    def to_effect(self):
        return {'op':EVENT_PLAYER_SET_OPERATION,EVENT_PLAYER_SET_VERSION_FIELD:1,
            'predicate':ObjectQuerySpec(zones=('battlefield',),controller=DEFENDING_PLAYER_REFERENCE,types_all=('creature',)).to_dict(),
            'power':self.power,'toughness':self.toughness}

    @classmethod
    def from_effect(cls,value,*,resolved=False):
        fields={'op',EVENT_PLAYER_SET_VERSION_FIELD,'predicate','power','toughness'}
        if not isinstance(value,Mapping) or set(value)!=fields or value.get('op')!=EVENT_PLAYER_SET_OPERATION:
            raise ValueError('Event-player modifier instruction fields are closed')
        spec=cls(value['power'],value['toughness'],value[EVENT_PLAYER_SET_VERSION_FIELD])
        query=ObjectQuerySpec.from_dict(value['predicate'])
        player=query.controller
        if (not resolved and player!=DEFENDING_PLAYER_REFERENCE or resolved and
                (type(player) is not str or not player or player.startswith('$'))):
            raise ValueError('Event-player modifier requires its proved symbolic or bound player')
        expected=ObjectQuerySpec(zones=('battlefield',),controller=player,types_all=('creature',))
        if query!=expected or dict(value['predicate'])!=expected.to_dict():
            raise ValueError('Event-player modifier accepts only the complete creature set of its represented player')
        return spec

    def bound_effect(self,player):
        value=self.to_effect();value['predicate']=ObjectQuerySpec(zones=('battlefield',),controller=player,types_all=('creature',)).to_dict()
        self.from_effect(value,resolved=True)
        return {k:v for k,v in value.items() if k!=EVENT_PLAYER_SET_VERSION_FIELD}
