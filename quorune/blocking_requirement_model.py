from __future__ import annotations

"""A resolved requirement relating two incarnations in one combat."""

from dataclasses import dataclass
from typing import Mapping

from .continuous_effect_model import ContinuousObjectIdentity


BLOCKING_REQUIREMENT_CAPABILITY='combat.block.requirement_this_combat'
BLOCKING_REQUIREMENT_OPERATION='require_blocking_this_combat'


@dataclass(frozen=True,slots=True)
class ResolvedBlockingRequirement:
    requirement_id:str
    attacker:ContinuousObjectIdentity
    blocker:ContinuousObjectIdentity

    def __post_init__(self):
        if type(self.requirement_id) is not str or not self.requirement_id:
            raise ValueError('Blocking requirements need a stable identity')
        if (not isinstance(self.attacker,ContinuousObjectIdentity) or not isinstance(self.blocker,ContinuousObjectIdentity)
                or self.attacker.object_id==self.blocker.object_id):
            raise ValueError('Blocking requirements need two distinct exact incarnations')

    def to_dict(self):
        return {'schema_version':1,'requirement_id':self.requirement_id,'attacker':self.attacker.to_dict(),'blocker':self.blocker.to_dict()}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','requirement_id','attacker','blocker'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise ValueError('Blocking requirements use a closed version-one schema')
        return cls(value['requirement_id'],ContinuousObjectIdentity.from_dict(value['attacker']),ContinuousObjectIdentity.from_dict(value['blocker']))


def normalize_blocking_requirements(values):
    if not isinstance(values,(tuple,list)):raise ValueError('Combat blocking requirements must be an array')
    result=tuple(v if isinstance(v,ResolvedBlockingRequirement) else ResolvedBlockingRequirement.from_dict(v) for v in values)
    if len({v.requirement_id for v in result})!=len(result):raise ValueError('Blocking requirement identities must be unique')
    return result
