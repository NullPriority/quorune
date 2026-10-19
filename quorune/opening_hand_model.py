from __future__ import annotations

"""Versioned immutable identity and visibility frames for CR 103.6."""

from dataclasses import dataclass,asdict
from typing import Mapping
from .errors import GameRuleError


@dataclass(frozen=True,slots=True)
class OpeningHandSubject:
    object_id: str
    logical_object_id: str
    ref: str
    player: str
    action_kind: str='battlefield'
    semantic_key: str=''

    def __post_init__(self):
        if (any(type(v) is not str or not v for v in (self.object_id,self.logical_object_id,self.ref,self.player))
                or self.action_kind not in {'battlefield','reveal'} or type(self.semantic_key) is not str
                or self.action_kind=='reveal' and not self.semantic_key):
            raise GameRuleError('Opening-hand source identity is malformed')

    def to_dict(self,version=1):
        value=asdict(self)
        if version==1:
            value.pop('action_kind');value.pop('semantic_key')
        return value

    @classmethod
    def from_dict(cls,value,version=1):
        fields={'object_id','logical_object_id','ref','player'}|({'action_kind','semantic_key'} if version==2 else set())
        if not isinstance(value,Mapping) or set(value)!=fields:raise GameRuleError('Opening-hand subject fields changed')
        return cls(**value)


@dataclass(frozen=True,slots=True)
class OpeningHandRevealed:
    subject: OpeningHandSubject
    prior_revealed_to: tuple[str,...]

    def __post_init__(self):
        if (not isinstance(self.subject,OpeningHandSubject) or self.subject.action_kind!='reveal'
                or type(self.prior_revealed_to) is not tuple or len(set(self.prior_revealed_to))!=len(self.prior_revealed_to)
                or any(type(v) is not str or not v for v in self.prior_revealed_to)):
            raise GameRuleError('Opening-hand revealed identity is malformed')

    def to_dict(self):return {'subject':self.subject.to_dict(2),'prior_revealed_to':list(self.prior_revealed_to)}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'subject','prior_revealed_to'} or type(value['prior_revealed_to']) is not list:
            raise GameRuleError('Opening-hand revealed fields changed')
        return cls(OpeningHandSubject.from_dict(value['subject'],2),tuple(value['prior_revealed_to']))


@dataclass(frozen=True,slots=True)
class OpeningHandFrame:
    seats: tuple[str,...]
    subjects: tuple[OpeningHandSubject,...]
    index: int=0
    version: int=1
    revealed: tuple[OpeningHandRevealed,...]=()

    def __post_init__(self):
        if (type(self.version) is not int or self.version not in {1,2} or type(self.seats) is not tuple or not self.seats
                or len(set(self.seats))!=len(self.seats) or any(type(v) is not str or not v for v in self.seats)
                or type(self.index) is not int or not 0<=self.index<=len(self.seats)
                or type(self.subjects) is not tuple or any(not isinstance(v,OpeningHandSubject) for v in self.subjects)
                or any(v.player not in self.seats[self.index:] for v in self.subjects)
                or len({(v.object_id,v.action_kind,v.semantic_key) for v in self.subjects})!=len(self.subjects)
                or len({(v.ref,v.action_kind,v.semantic_key) for v in self.subjects})!=len(self.subjects)
                or len({(v.logical_object_id,v.action_kind,v.semantic_key) for v in self.subjects})!=len(self.subjects)
                or type(self.revealed) is not tuple or any(not isinstance(v,OpeningHandRevealed) for v in self.revealed)
                or len({(v.subject.object_id,v.subject.logical_object_id) for v in self.revealed})!=len(self.revealed)
                or any(v.subject.player not in self.seats for v in self.revealed)
                or self.version==1 and (self.revealed or any(s.action_kind!='battlefield' or s.semantic_key for s in self.subjects))):
            raise GameRuleError('Opening-hand continuation is malformed')

    def to_dict(self):
        value={'schema_version':self.version,'seats':list(self.seats),'subjects':[s.to_dict(self.version) for s in self.subjects],'index':self.index}
        if self.version==2:value['revealed']=[v.to_dict() for v in self.revealed]
        return value

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping):raise GameRuleError('Opening-hand continuation must be an object')
        version=value.get('schema_version')
        fields={'schema_version','seats','subjects','index'}|({'revealed'} if version==2 else set())
        if (type(version) is not int or version not in {1,2} or set(value)!=fields
                or type(value['seats']) is not list or type(value['subjects']) is not list
                or version==2 and type(value['revealed']) is not list):
            raise GameRuleError('Opening-hand continuation fields changed')
        return cls(tuple(value['seats']),tuple(OpeningHandSubject.from_dict(s,version) for s in value['subjects']),
            value['index'],version,tuple(OpeningHandRevealed.from_dict(v) for v in value.get('revealed',())))

    def validate_state(self,host):
        state=host.state
        if (state.started or state.current_turn is not None or state.turn_sequence!=0
                or state.phase!='setup' or state.step!='mulligan' or state.phase_index!=0
                or state.priority_player is not None or state.stack or tuple(state.turn_order)!=self.seats
                or not all(p.kept_hand for p in state.players.values())):
            raise GameRuleError('Opening-hand actions require the completed mulligan window')
