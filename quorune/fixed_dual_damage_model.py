"""Closed fixed damage to one target and the resolving controller."""
from collections.abc import Mapping
from dataclasses import dataclass

FIXED_DUAL_DAMAGE_FIELD = 'fixed_target_controller_damage'
FIXED_DUAL_DAMAGE_MECHANIC = 'fixed-target-controller-damage'
FIXED_DUAL_DAMAGE_CAPABILITY = 'damage.batch.fixed_target_and_controller'


@dataclass(frozen=True,slots=True)
class FixedTargetControllerDamageSpec:
    target_amount: int
    controller_amount: int

    def __post_init__(self):
        if any(type(value) is not int or value<=0 for value in (self.target_amount,self.controller_amount)):
            raise ValueError('Fixed target/controller damage requires two positive integer amounts')

    @property
    def total(self):return self.target_amount+self.controller_amount

    def to_dict(self):
        return {'schema_version':1,'target_amount':self.target_amount,'controller_amount':self.controller_amount}

    @classmethod
    def from_dict(cls,value,*,bound=False):
        fields = {'schema_version','target_amount','controller_amount'}|({'recipients'} if bound else set())
        if (not isinstance(value,Mapping) or set(value)!=fields
                or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise ValueError('Fixed target/controller damage has a closed version-one descriptor')
        return cls(value['target_amount'],value['controller_amount'])
