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
NEXT_TURN_NONHAND_CAPABILITY = "casting.prohibition.opponents_nonhand_until_next_turn"


@dataclass(frozen=True,slots=True)
class TemporaryCastRestrictionSpec:
    player_scope: str
    spell_kind: str='any'
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version not in {1, 2}
                or self.player_scope not in {'opponents','all','target'} or self.spell_kind not in {'any','creature','noncreature'}
                or self.nonhand_until_next_turn and (self.player_scope, self.spell_kind) != ("opponents", "any")):
            raise ContinuousEffectError('Temporary cast restriction scope, subject or version is unsupported')

    @property
    def nonhand_until_next_turn(self) -> bool:
        return self.schema_version == 2

    @property
    def duration(self) -> ContinuousEffectDuration:
        return (ContinuousEffectDuration.UNTIL_NEXT_TURN if self.nonhand_until_next_turn
                else ContinuousEffectDuration.UNTIL_END_OF_TURN)

    def to_dict(self):return {'schema_version':self.schema_version,'player_scope':self.player_scope,'spell_kind':self.spell_kind}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','player_scope','spell_kind'}:
            raise ContinuousEffectError('Temporary cast restriction fields are incomplete or unknown')
        return cls(value['player_scope'],value['spell_kind'],value['schema_version'])


def opponents_nonhand_until_next_turn_effect():
    restriction = TemporaryCastRestrictionSpec("opponents", schema_version=2)
    return {"op": OPERATION, "actor": "$controller", "player": None, "restriction": restriction.to_dict()}


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
                or self.duration is not self.restriction.duration):
            raise ContinuousEffectError('Resolved casting rule requires identity, controller, timestamp and its typed duration')
        if (self.restriction.player_scope=='target') != (type(self.target_player) is str and bool(self.target_player)):
            raise ContinuousEffectError('Target-player casting rule requires exactly its chosen seat')
        if self.restriction.player_scope!='target' and self.target_player is not None:
            raise ContinuousEffectError('Nontarget casting rule cannot store a target seat')

    @property
    def expires_on_next_turn_of(self) -> str | None:
        return self.controller if self.restriction.nonhand_until_next_turn else None

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


NEXT_TURN_SORCERY_TIMING_KIND = "resolution_next_turn_sorcery_timing"


@dataclass(frozen=True, slots=True)
class ResolvedNextTurnSorceryTiming:
    """A casting permission on one player until that player's next turn."""

    effect_id: str
    source_id: str
    timestamp: int
    controller: str

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.effect_id, self.source_id, self.controller))
                or type(self.timestamp) is not int or self.timestamp < 0):
            raise ContinuousEffectError("Next-turn sorcery timing requires identity, timestamp and controller")

    @property
    def duration(self) -> ContinuousEffectDuration:
        return ContinuousEffectDuration.UNTIL_NEXT_TURN

    @property
    def expires_on_next_turn_of(self) -> str:
        return self.controller

    def to_dict(self):
        return {
            "effect_kind": NEXT_TURN_SORCERY_TIMING_KIND, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id,
            "timestamp": self.timestamp, "controller": self.controller,
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp", "controller"}
        if (not isinstance(value, Mapping) or set(value) != fields
                or value["effect_kind"] != NEXT_TURN_SORCERY_TIMING_KIND
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ContinuousEffectError("Next-turn sorcery timing journal fields are closed")
        return cls(value["effect_id"], value["source_id"], value["timestamp"], value["controller"])

    @property
    def fingerprint(self) -> str:
        return immutable_fingerprint(self.to_dict())
