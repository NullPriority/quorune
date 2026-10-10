from __future__ import annotations

"""Closed source-local CR510 assignment rules; no characteristic mutation."""

from dataclasses import dataclass
from typing import Any, Mapping
from .object_predicate import ObjectQuerySpec

TOUGHNESS_ASSIGNMENT_CAPABILITY='combat.damage.assignment.toughness'
TOUGHNESS_ASSIGNMENT_HANDLER='ability.static.toughness-assignment.v1'
TEMPORARY_TOUGHNESS_MECHANIC='temporary-toughness-assignment'


@dataclass(frozen=True,slots=True)
class ToughnessAssignmentSpec:
    scope: str
    predicate: ObjectQuerySpec
    toughness_greater_than_power: bool = False
    during_controller_turn: bool = False
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1 or self.scope not in {'self','attached','all','controller'}:
            raise ValueError('Toughness assignment scope or version is unsupported')
        if not isinstance(self.predicate,ObjectQuerySpec) or self.predicate.zones!=('battlefield',) or self.predicate.types_all!=('creature',):
            raise ValueError('Toughness assignment requires a current creature predicate')
        if any(type(value) is not bool for value in (self.toughness_greater_than_power,self.during_controller_turn)):
            raise ValueError('Toughness assignment flags must be strict booleans')
        if self.predicate not in (ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)),
            ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_all=('defender',)),
            ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_all=('vigilance',))):
            raise ValueError('Toughness assignment predicate is outside the closed family')

    def to_dict(self):
        return {'schema_version':self.schema_version,'scope':self.scope,'predicate':self.predicate.to_dict(),
            'toughness_greater_than_power':self.toughness_greater_than_power,'during_controller_turn':self.during_controller_turn}

    @classmethod
    def from_dict(cls,value:Mapping[str,Any]):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','scope','predicate','toughness_greater_than_power','during_controller_turn'}:
            raise ValueError('Toughness assignment rule has a closed schema')
        return cls(scope=value['scope'],predicate=ObjectQuerySpec.from_dict(value['predicate']),
            toughness_greater_than_power=value['toughness_greater_than_power'],during_controller_turn=value['during_controller_turn'],schema_version=value['schema_version'])
