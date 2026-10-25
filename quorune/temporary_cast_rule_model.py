from __future__ import annotations

"""Immutable resolution-created player casting rules in the duration journal."""

from dataclasses import dataclass
from typing import Mapping
from .continuous_effect_model import ContinuousEffectDuration,ContinuousEffectError
from .replacement.immutable import immutable_fingerprint

EFFECT_KIND='resolution_cast_restriction'
OPERATION='prohibit_casting_until_end_of_turn'
MECHANIC='temporary-cast-prohibition'
CAPABILITY='casting.prohibition.resolution_until_end_of_turn'


@dataclass(frozen=True,slots=True)
class TemporaryCastRestrictionSpec:
    player_scope: str
    spell_kind: str='any'
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or self.player_scope not in {'opponents','all','target'} or self.spell_kind not in {'any','creature','noncreature'}):
            raise ContinuousEffectError('Temporary cast restriction scope, subject or version is unsupported')

    def to_dict(self):return {'schema_version':self.schema_version,'player_scope':self.player_scope,'spell_kind':self.spell_kind}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','player_scope','spell_kind'}:
            raise ContinuousEffectError('Temporary cast restriction fields are incomplete or unknown')
        return cls(value['player_scope'],value['spell_kind'],value['schema_version'])


@dataclass(frozen=True,slots=True)
class TemporaryCastRestrictionIntent:
    actor: str
    restriction: TemporaryCastRestrictionSpec
    target_player: str | None
    source_id: str

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.actor,self.source_id)) or not isinstance(self.restriction,TemporaryCastRestrictionSpec):
            raise ValueError('Casting restriction requires a typed resolving source')
        if (self.restriction.player_scope=='target') != (type(self.target_player) is str and bool(self.target_player)):
            raise ValueError('Casting restriction target seat is inconsistent')
        if self.restriction.player_scope!='target' and self.target_player is not None:
            raise ValueError('Untargeted casting restriction cannot store a target')


@dataclass(frozen=True,slots=True)
class ResolvedCastRestriction:
    effect_id: str
    source_id: str
    timestamp: int
    controller: str
    restriction: TemporaryCastRestrictionSpec
    target_player: str | None=None
    duration: ContinuousEffectDuration=ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if (any(type(v) is not str or not v for v in (self.effect_id,self.source_id,self.controller))
                or type(self.timestamp) is not int or self.timestamp<0 or not isinstance(self.restriction,TemporaryCastRestrictionSpec)
                or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN):
            raise ContinuousEffectError('Resolved casting rule requires identity, controller, timestamp and end-of-turn duration')
        if (self.restriction.player_scope=='target') != (type(self.target_player) is str and bool(self.target_player)):
            raise ContinuousEffectError('Target-player casting rule requires exactly its chosen seat')
        if self.restriction.player_scope!='target' and self.target_player is not None:
            raise ContinuousEffectError('Nontarget casting rule cannot store a target seat')

    def to_dict(self):
        return {'effect_kind':EFFECT_KIND,'effect_id':self.effect_id,'source_id':self.source_id,'timestamp':self.timestamp,
            'controller':self.controller,'restriction':self.restriction.to_dict(),'target_player':self.target_player,'duration':self.duration.value}

    @classmethod
    def from_dict(cls,value):
        fields={'effect_kind','effect_id','source_id','timestamp','controller','restriction','target_player','duration'}
        if not isinstance(value,Mapping) or set(value)!=fields or value['effect_kind']!=EFFECT_KIND:
            raise ContinuousEffectError('Resolved casting-rule fields are incomplete or unknown')
        return cls(value['effect_id'],value['source_id'],value['timestamp'],value['controller'],
            TemporaryCastRestrictionSpec.from_dict(value['restriction']),value['target_player'],ContinuousEffectDuration(value['duration']))

    @property
    def fingerprint(self):return immutable_fingerprint(self.to_dict())
