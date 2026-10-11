from __future__ import annotations

"""One printed CR 702.154 Enlist instance."""

from dataclasses import dataclass

ENLIST_CAPABILITY='combat.attack.enlist'
ENLIST_HANDLER='ability.static.enlist.v1'


@dataclass(frozen=True,slots=True)
class EnlistSpec:
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:raise ValueError('Unsupported Enlist schema')

    def to_dict(self):return {'schema_version':1}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict) or set(value)!={'schema_version'}:raise ValueError('Enlist has a closed schema')
        return cls(**value)
