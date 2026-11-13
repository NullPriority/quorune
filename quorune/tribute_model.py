from __future__ import annotations

"""Immutable intrinsic Tribute and its identity-bound entry outcome."""

from collections.abc import Mapping
from dataclasses import dataclass

TRIBUTE_CAPABILITY = 'counter.producer.tribute_entry'
TRIBUTE_HANDLER = 'replacement.zone.tribute-entry.v1'
TRIBUTE_ENTRY_EVENT = 'entry.tribute'
TRIBUTE_ENTRY_OUTCOMES = 'tribute_entry_outcomes'
TRIBUTE_UNPAID_FIELD = 'tribute_unpaid'


def _identity(value, name):
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f'Tribute requires a canonical {name}')


@dataclass(frozen=True, slots=True)
class TributeSpec:
    amount: int
    link_id: str
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported Tribute specification version')
        if type(self.amount) is not int or self.amount < 1:
            raise ValueError('Represented Tribute requires a positive fixed counter amount')
        _identity(self.link_id,'ability link')

    def to_dict(self):
        return {'schema_version':1,'amount':self.amount,'link_id':self.link_id}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','amount','link_id'}:
            raise ValueError('Tribute specification fields are closed')
        return cls(value['amount'],value['link_id'],value['schema_version'])

    def descriptor(self):
        return {'handler_id':TRIBUTE_HANDLER,'event':'zone.change',**self.to_dict()}


@dataclass(frozen=True, slots=True)
class TributeEntryOutcome:
    spec: TributeSpec
    entry_id: str
    logical_object_id: str
    controller: str
    opponent: str
    paid: bool

    def __post_init__(self):
        if not isinstance(self.spec,TributeSpec) or type(self.paid) is not bool:
            raise ValueError('Tribute outcome requires its typed specification and payment fact')
        for field in ('entry_id','logical_object_id','controller','opponent'):
            _identity(getattr(self,field),field)
        if self.controller == self.opponent:
            raise ValueError('Tribute cannot select its entering controller as opponent')

    def to_dict(self):
        return {'spec':self.spec.to_dict(),'entry_id':self.entry_id,
            'logical_object_id':self.logical_object_id,'controller':self.controller,
            'opponent':self.opponent,'paid':self.paid}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'spec','entry_id','logical_object_id','controller','opponent','paid'}:
            raise ValueError('Tribute entry outcome fields are closed')
        return cls(TributeSpec.from_dict(value['spec']),value['entry_id'],value['logical_object_id'],
            value['controller'],value['opponent'],value['paid'])

    def is_unpaid_entry(self,*,link_id,logical_object_id):
        return self.spec.link_id==link_id and self.logical_object_id==logical_object_id and not self.paid
