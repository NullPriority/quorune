from __future__ import annotations

"""Closed event roles for one permanent departure and its physical arrivals."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from .continuous_effect_model import ContinuousObjectIdentity
from .permanent_composition import _validate_identity


class CompositionZoneRole(str,Enum):
    PERMANENT='permanent'
    COMPONENT='component'


@dataclass(frozen=True,slots=True)
class CompositionZoneFact:
    parent: ContinuousObjectIdentity
    role: CompositionZoneRole
    member_count: int
    schema_version: int=1

    def __post_init__(self):
        _validate_identity(self.parent)
        if (not isinstance(self.role,CompositionZoneRole) or type(self.member_count) is not int or self.member_count<2
                or type(self.schema_version) is not int or self.schema_version!=1):
            raise ValueError('A composition occurrence requires its typed role and physical group size')

    @property
    def permanent_departure(self):
        return self.role is CompositionZoneRole.PERMANENT

    @property
    def physical_arrival(self):
        return self.role is CompositionZoneRole.COMPONENT

    def validate_occurrence(self,occurrence):
        if (occurrence.origin!='battlefield' or occurrence.destination not in
                ('graveyard','exile','hand','library','command','outside')
                or occurrence.transition_kind.value not in ('ordinary','sacrifice')):
            raise ValueError('Composition occurrences require a battlefield departure scope')
        if self.permanent_departure:
            if (occurrence.object_id!=self.parent.object_id
                    or occurrence.previous_logical_object_id!=self.parent.logical_object_id):
                raise ValueError('Permanent departure does not match its retained parent incarnation')
        elif occurrence.transition_kind.value!='ordinary':
            raise ValueError('Physical parts cannot separately claim permanent sacrifice or spell transitions')

    def to_dict(self):
        return {'schema_version':self.schema_version,'parent':{'object_id':self.parent.object_id,
            'logical_object_id':self.parent.logical_object_id},'role':self.role.value,'member_count':self.member_count}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','parent','role','member_count'}
                or not isinstance(value['parent'],Mapping)
                or set(value['parent'])!={'object_id','logical_object_id'}):
            raise ValueError('Composition occurrences have a closed wire shape')
        return cls(ContinuousObjectIdentity(**dict(value['parent'])),CompositionZoneRole(value['role']),
            value['member_count'],value['schema_version'])
