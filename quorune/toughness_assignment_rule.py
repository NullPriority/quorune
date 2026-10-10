from __future__ import annotations

"""Resolution-created combat quantity rule on selected incarnations."""

from dataclasses import dataclass
from typing import Any,Mapping
from .continuous_effect_model import ContinuousEffectDuration,ContinuousEffectError,ContinuousObjectIdentity
from .replacement.immutable import immutable_fingerprint

TOUGHNESS_RULE_KIND='resolution_toughness_assignment'


@dataclass(frozen=True,slots=True)
class ResolvedToughnessAssignmentRule:
    effect_id: str
    source_id: str
    timestamp: int
    locked_objects: tuple[ContinuousObjectIdentity,...]
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.effect_id,self.source_id)) or type(self.timestamp) is not int or self.timestamp<0:
            raise ContinuousEffectError('Resolved toughness rule identity is malformed')
        locked=tuple(self.locked_objects)
        if not locked or len(set(locked))!=len(locked) or any(not isinstance(value,ContinuousObjectIdentity) for value in locked):
            raise ContinuousEffectError('Resolved toughness rule requires unique current incarnations')
        object.__setattr__(self,'locked_objects',locked)
        if self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN:raise ContinuousEffectError('Resolved toughness rules expire at end of turn')

    def to_dict(self):
        return {'effect_kind':TOUGHNESS_RULE_KIND,'effect_id':self.effect_id,'source_id':self.source_id,'timestamp':self.timestamp,
            'locked_objects':[value.to_dict() for value in self.locked_objects],'duration':self.duration.value}

    @classmethod
    def from_dict(cls,value:Mapping[str,Any]):
        if not isinstance(value,Mapping) or set(value)!={'effect_kind','effect_id','source_id','timestamp','locked_objects','duration'} or value['effect_kind']!=TOUGHNESS_RULE_KIND or not isinstance(value['locked_objects'],list):
            raise ContinuousEffectError('Resolved toughness rules have a closed schema')
        return cls(effect_id=value['effect_id'],source_id=value['source_id'],timestamp=value['timestamp'],
            locked_objects=tuple(ContinuousObjectIdentity.from_dict(raw) for raw in value['locked_objects']),duration=ContinuousEffectDuration(value['duration']))

    @property
    def fingerprint(self):return immutable_fingerprint(self.to_dict())


def active_resolved_toughness_rule(state,card):
    if card.zone!='battlefield' or card.phased_out:return False
    identity=ContinuousObjectIdentity(card.object_id,card.logical_object_id)
    return any(isinstance(effect,ResolvedToughnessAssignmentRule) and identity in effect.locked_objects for effect in state.continuous_effects or ())
