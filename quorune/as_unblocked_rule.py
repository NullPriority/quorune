from __future__ import annotations

"""Resolution-created CR609.4 assignment rules, separate from abilities."""

from dataclasses import dataclass
from typing import Any, Mapping
from .continuous_effect_model import ContinuousEffectDuration,ContinuousEffectError,ContinuousObjectIdentity
from .replacement.immutable import immutable_fingerprint

AS_UNBLOCKED_RULE_KIND='resolution_as_unblocked_assignment'


@dataclass(frozen=True,slots=True)
class AsUnblockedAssignmentRule:
    effect_id: str
    source_id: str
    timestamp: int
    controller: str | None = None
    locked_objects: tuple[ContinuousObjectIdentity,...] = ()
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.effect_id,self.source_id)) or type(self.timestamp) is not int or self.timestamp<0:
            raise ContinuousEffectError('As-unblocked rule identity is malformed')
        locked=tuple(self.locked_objects)
        if not all(isinstance(value,ContinuousObjectIdentity) for value in locked) or len(set(locked))!=len(locked):
            raise ContinuousEffectError('As-unblocked rule requires unique locked incarnations')
        if (self.controller is not None)==bool(locked) or self.controller is not None and (type(self.controller) is not str or not self.controller):
            raise ContinuousEffectError('As-unblocked rule requires one controller set or one locked object set')
        object.__setattr__(self,'locked_objects',locked)
        if self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN:raise ContinuousEffectError('As-unblocked rules expire at end of turn')

    def to_dict(self):
        return {'effect_kind':AS_UNBLOCKED_RULE_KIND,'effect_id':self.effect_id,'source_id':self.source_id,'timestamp':self.timestamp,
            'controller':self.controller,'locked_objects':[value.to_dict() for value in self.locked_objects],'duration':self.duration.value}

    @classmethod
    def from_dict(cls,value:Mapping[str,Any]):
        if not isinstance(value,Mapping) or set(value)!={'effect_kind','effect_id','source_id','timestamp','controller','locked_objects','duration'} or value['effect_kind']!=AS_UNBLOCKED_RULE_KIND or not isinstance(value['locked_objects'],list):
            raise ContinuousEffectError('As-unblocked rules have a closed schema')
        return cls(effect_id=value['effect_id'],source_id=value['source_id'],timestamp=value['timestamp'],controller=value['controller'],
            locked_objects=tuple(ContinuousObjectIdentity.from_dict(raw) for raw in value['locked_objects']),duration=ContinuousEffectDuration(value['duration']))

    @property
    def fingerprint(self):return immutable_fingerprint(self.to_dict())


def active_as_unblocked_rule(state,card):
    if card.zone!='battlefield' or card.phased_out:return False
    identity=ContinuousObjectIdentity(card.object_id,card.logical_object_id)
    return any(isinstance(effect,AsUnblockedAssignmentRule) and (effect.controller==card.controller if effect.controller is not None else identity in effect.locked_objects)
        for effect in state.continuous_effects or ())
