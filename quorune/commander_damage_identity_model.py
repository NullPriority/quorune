"""Immutable physical commander identities in one merged damage source."""
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True,slots=True)
class PhysicalCommanderDamageIdentity:
    designation_id: str
    oracle_id: str

    def __post_init__(self):
        if any(type(value) is not str or not value or value.strip()!=value
                for value in (self.designation_id,self.oracle_id)):
            raise ValueError('Physical commander damage identity requires exact nonempty identifiers')

    def to_dict(self):
        return {'designation_id':self.designation_id,'oracle_id':self.oracle_id}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'designation_id','oracle_id'}:
            raise ValueError('Physical commander damage identity fields are closed')
        return cls(**value)


@dataclass(frozen=True,slots=True)
class PhysicalCommanderDamageIdentities:
    commanders: tuple[PhysicalCommanderDamageIdentity,...]
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.commanders,tuple) or not self.commanders
                or any(not isinstance(value,PhysicalCommanderDamageIdentity) for value in self.commanders)):
            raise ValueError('Physical commander source requires its closed nonempty identity set')
        if len({value.designation_id for value in self.commanders})!=len(self.commanders):
            raise ValueError('Physical commander designations must be unique')
        object.__setattr__(self,'commanders',tuple(sorted(self.commanders,key=lambda value:value.designation_id)))

    def validate_source(self,*,is_commander,designation_id,oracle_id):
        if is_commander is not True:
            raise ValueError('Only a commander source may contain physical commander identities')
        if designation_id is not None:
            matching=next((value for value in self.commanders if value.designation_id==designation_id),None)
            if matching is None or oracle_id is not None and matching.oracle_id!=oracle_id:
                raise ValueError('Scalar and physical commander identities disagree')

    def to_dict(self):
        return {'schema_version':self.schema_version,'commanders':[value.to_dict() for value in self.commanders]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','commanders'}
                or not isinstance(value['commanders'],(list,tuple))):
            raise ValueError('Physical commander source fields are closed')
        return cls(tuple(PhysicalCommanderDamageIdentity.from_dict(row) for row in value['commanders']),value['schema_version'])
