"""Printed fixed damage shares, independent of targeting and mutation."""
from collections.abc import Mapping
from dataclasses import dataclass

FIXED_DAMAGE_ROLE_FIELD='fixed_target_role_damage'
FIXED_DAMAGE_ROLE_MECHANIC='fixed-target-role-damage'
FIXED_DAMAGE_ROLE_CAPABILITY='damage.batch.fixed_target_roles'


@dataclass(frozen=True,slots=True)
class FixedDamageRoleSpec:
    amounts: tuple[int,...]
    minimums: tuple[int,...]
    maximums: tuple[int,...]

    def __post_init__(self):
        if (not isinstance(self.amounts,(list,tuple)) or not 2<=len(self.amounts)<=4
                or any(type(n) is not int or n<=0 for n in self.amounts)):
            raise ValueError('Fixed damage roles require two to four positive printed amounts')
        object.__setattr__(self,'amounts',tuple(self.amounts))
        for field in ('minimums','maximums'):
            values=getattr(self,field)
            if not isinstance(values,(list,tuple)) or len(values)!=len(self.amounts):
                raise ValueError('Fixed damage role bounds must match their printed shares')
            object.__setattr__(self,field,tuple(values))
        if self.minimums[0]!=1 or self.maximums[0]!=1 or any(
                type(low) is not int or type(high) is not int or (low,high) not in {(1,1),(0,1),(0,2)}
                for low,high in zip(self.minimums,self.maximums,strict=True)):
            raise ValueError('Fixed damage roles require one mandatory first target and closed later bounds')

    @property
    def total(self):return sum(a*n for a,n in zip(self.amounts,self.maximums,strict=True))

    def to_dict(self):return {'schema_version':1,'amounts':list(self.amounts),'minimums':list(self.minimums),'maximums':list(self.maximums)}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','amounts','minimums','maximums'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise ValueError('Fixed damage roles require their closed version-one descriptor')
        return cls(value['amounts'],value['minimums'],value['maximums'])


def fixed_damage_role_spec(effect):
    if (not isinstance(effect,Mapping) or set(effect)!={'op','source','amount',FIXED_DAMAGE_ROLE_FIELD}
            or effect['op']!='damage_fixed_set' or effect['source']!='$source'):
        raise ValueError('Fixed damage roles require one complete source instruction')
    spec=FixedDamageRoleSpec.from_dict(effect[FIXED_DAMAGE_ROLE_FIELD])
    if type(effect['amount']) is not int or effect['amount']!=spec.total:
        raise ValueError('Fixed damage role total differs from its printed shares')
    return spec
